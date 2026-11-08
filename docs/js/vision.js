// KnightSight — © 2026 KnightSight. All rights reserved.
//
// The vision step: camera frame -> per-square [empty, white, black] probabilities.
//
// 1. Find the green-and-white board without markers: green squares become blobs,
//    a lattice is fitted through them, turned so your side is at the bottom, and
//    corrected for a slightly bent board.
// 2. Warp the board top-down and describe each square (brightness, colour, edges),
//    looking at the lower "base" band where a piece stands and the upper "body" band.
// 3. A small classifier says empty / white / black per square. It needs no labels:
//    games start from the starting position, which labels all 64 squares, and every
//    confirmed position afterwards teaches it again, so it follows the room's light.
//
// View cells (u, v): u = file from the left as seen, v = row from the near side.

import { TUNING, WHITE, BLACK, EMPTY } from "./tracker.js";

const MAX_SIDE = 1280;           // frames are downscaled to this long side first
const MIN_BLOBS = 6;             // fewer green squares than this = no board
const ASSIGN_TOL = 0.3;          // lattice units: how close a blob centre must land to a cell centre
export const CELL_PX = 64;       // warped board: pixels per square
const BASE = [0.2, 0.8, 0.5, 0.95];   // regions inside a square (x0, x1, y0, y1); y = 0 is the far edge
const BODY = [0.25, 0.75, 0.1, 0.5];
const MIN_VALID = 0.8;           // a square counts as visible if this much of it is inside the frame
const MAX_OCCLUDED = 0.12;       // ...and no more of its base band than this is a sleeve or bright object
const BUFFER_PER_CLASS = 800;
const F_BASE_DARK = 5, F_BASE_GREEN = 7, F_BASE_EDGE = 8, F_BODY_DARK = 13;

let cv = null;

/** Load OpenCV.js once; resolves when it is ready. */
export function loadOpenCV(url) {
  if (cv) return Promise.resolve(cv);
  return new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = url;
    s.async = true;
    s.onerror = () => reject(new Error("Could not load OpenCV"));
    s.onload = async () => {
      let lib = window.cv;
      if (lib instanceof Promise) lib = await lib;
      else if (!lib.Mat) await new Promise((r) => { lib.onRuntimeInitialized = r; });
      cv = lib;
      resolve(cv);
    };
    document.head.appendChild(s);
  });
}

// ---------- small maths (NumPy semantics where it matters) ----------

/** NumPy rounds halves to even. */
function npRound(x) {
  const r = Math.round(x);
  return Math.abs(x % 1) === 0.5 && r % 2 !== 0 ? r - 1 : r;
}
const pyMod = (a, n) => ((a % n) + n) % n;
const median = (xs) => {
  const s = [...xs].sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
};

function inv3(m) {
  const [a, b, c, d, e, f, g, h, i] = m;
  const A = e * i - f * h, B = -(d * i - f * g), C = d * h - e * g;
  const det = a * A + b * B + c * C;
  if (!Number.isFinite(det) || Math.abs(det) < 1e-300) throw new Error("singular");
  return [A / det, -(b * i - c * h) / det, (b * f - c * e) / det,
          B / det, (a * i - c * g) / det, -(a * f - c * d) / det,
          C / det, -(a * h - b * g) / det, (a * e - b * d) / det];
}

function mul3(p, q) {
  const r = new Array(9).fill(0);
  for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) for (let k = 0; k < 3; k++) r[i * 3 + j] += p[i * 3 + k] * q[k * 3 + j];
  return r;
}

/** cv2.perspectiveTransform for an array of [x, y]. */
function project(H, pts) {
  return pts.map(([x, y]) => {
    const w = H[6] * x + H[7] * y + H[8];
    return [(H[0] * x + H[1] * y + H[2]) / w, (H[3] * x + H[4] * y + H[5]) / w];
  });
}

const quadTerms = ([u, v]) => { u /= 8; v /= 8; return [1, u, v, u * u, u * v, v * v]; };

/** Solve a small dense system (Gaussian elimination with partial pivoting). */
function solve(A, B) {
  const n = A.length, m = B[0].length;
  const M = A.map((row, i) => [...row, ...B[i]]);
  for (let c = 0; c < n; c++) {
    let p = c;
    for (let r = c + 1; r < n; r++) if (Math.abs(M[r][c]) > Math.abs(M[p][c])) p = r;
    [M[c], M[p]] = [M[p], M[c]];
    if (Math.abs(M[c][c]) < 1e-300) throw new Error("singular");
    for (let r = 0; r < n; r++) {
      if (r === c) continue;
      const k = M[r][c] / M[c][c];
      if (k) for (let j = c; j < n + m; j++) M[r][j] -= k * M[c][j];
    }
  }
  return M.map((row, i) => row.slice(n).map((x) => x / M[i][i]));
}

function matToArray(mat) {
  const out = Array.from(mat.data64F);
  mat.delete();
  return out;
}

function pointsMat(pts, type) {
  const flat = pts.flat();
  return cv.matFromArray(pts.length, 1, type, flat);
}

// ---------- board finder ----------

export class BoardFit {
  constructor(H, coef, quality, visible, greenCells, rms, size) {
    Object.assign(this, { H, coef, quality, visible, greenCells, rms, size });
    this.Hinv = inv3(H);
  }

  /** View lattice points [[u, v], ...] -> image pixels. */
  toImage(uv) {
    const base = project(this.Hinv, uv);
    return base.map(([x, y], i) => {
      const t = quadTerms(uv[i]);
      let dx = 0, dy = 0;
      for (let k = 0; k < 6; k++) { dx += t[k] * this.coef[0][k]; dy += t[k] * this.coef[1][k]; }
      return [x + dx, y + dy];
    });
  }

  /** Top-down board (far side at the top) and a valid mask (pixels inside the frame), both 8*px square. */
  warp(image, px = CELL_PX) {
    const size = 8 * px;
    const uv = new Array(size * size);
    for (let r = 0; r < size; r++) for (let c = 0; c < size; c++) uv[r * size + c] = [(c + 0.5) / px, 8 - (r + 0.5) / px];
    const pts = this.toImage(uv);
    const mx = new cv.Mat(size, size, cv.CV_32F), my = new cv.Mat(size, size, cv.CV_32F);
    const valid = new Uint8Array(size * size);
    const [w, h] = this.size;
    for (let i = 0; i < pts.length; i++) {
      const x = Math.fround(pts[i][0]), y = Math.fround(pts[i][1]);
      mx.data32F[i] = x;
      my.data32F[i] = y;
      valid[i] = x >= 0 && x <= w - 1 && y >= 0 && y <= h - 1 ? 1 : 0;
    }
    const warped = new cv.Mat();
    cv.remap(image, warped, mx, my, cv.INTER_LINEAR, cv.BORDER_CONSTANT, new cv.Scalar());
    mx.delete();
    my.delete();
    return { warped, valid };
  }

  /** Median height in pixels of the visible squares. */
  cellHeights() {
    const out = [];
    for (let v = 0; v < 8; v++) for (let u = 0; u < 8; u++) {
      if (!this.visible[v][u]) continue;
      const [a, b] = this.toImage([[u + 0.5, v], [u + 0.5, v + 1]]);
      out.push(Math.abs(a[1] - b[1]));
    }
    return out.length ? median(out) : 0;
  }
}

function greenMask(image) {
  const hsv = new cv.Mat();
  cv.cvtColor(image, hsv, cv.COLOR_BGR2HSV);
  const lo = new cv.Mat(hsv.rows, hsv.cols, hsv.type(), [38, 25, 35, 0]);
  const hi = new cv.Mat(hsv.rows, hsv.cols, hsv.type(), [95, 255, 235, 0]);
  const mask = new cv.Mat();
  cv.inRange(hsv, lo, hi, mask);
  const k = cv.Mat.ones(3, 3, cv.CV_8U);
  cv.morphologyEx(mask, mask, cv.MORPH_OPEN, k);
  [hsv, lo, hi, k].forEach((m) => m.delete());
  return mask;
}

function orderCorners(corners, centre) {
  return corners
    .map((p) => ({ p, a: Math.atan2(p[1] - centre[1], p[0] - centre[0]) }))
    .sort((x, y) => x.a - y.a)
    .map((x) => x.p);
}

function blobs(mask) {
  // Diagonal neighbours touch at a corner; a light erosion separates them.
  const eroded = new cv.Mat();
  const k = cv.Mat.ones(3, 3, cv.CV_8U);
  cv.erode(mask, eroded, k);
  const contours = new cv.MatVector(), hierarchy = new cv.Mat();
  cv.findContours(eroded, contours, hierarchy, cv.RETR_EXTERNAL, cv.CHAIN_APPROX_SIMPLE);
  const out = [];
  for (let i = 0; i < contours.size(); i++) {
    const c = contours.get(i);
    const area = cv.contourArea(c);
    if (area < 30) { c.delete(); continue; }
    const hull = new cv.Mat();
    cv.convexHull(c, hull, false, true);
    const solidity = area / Math.max(cv.contourArea(hull), 1);
    if (solidity < 0.75) { c.delete(); hull.delete(); continue; }
    const approx = new cv.Mat();
    cv.approxPolyDP(hull, approx, 0.08 * cv.arcLength(hull, true), true);
    let corners;
    if (approx.rows === 4) {
      corners = [];
      for (let j = 0; j < 4; j++) corners.push([approx.data32S[2 * j], approx.data32S[2 * j + 1]]);
    } else {
      corners = cv.RotatedRect.points(cv.minAreaRect(c)).map((p) => [p.x, p.y]);
    }
    const m = cv.moments(c);
    const centre = [m.m10 / m.m00, m.m01 / m.m00];
    out.push({ centre, corners: orderCorners(corners, centre), area });
    [c, hull, approx].forEach((x) => x.delete());
  }
  [eroded, k, contours, hierarchy].forEach((x) => x.delete());
  if (!out.length) return [];
  const med = median(out.map((b) => b.area));
  return out.filter((b) => 0.2 * med < b.area && b.area < 4 * med);
}

/** Blob centres through H: [cells, ok] for cells of the dark-square parity. */
function assign(H, centres) {
  const p = project(H, centres).map(([x, y]) => [x - 0.5, y - 0.5]);
  const cells = p.map(([x, y]) => [npRound(x), npRound(y)]);
  const ok = p.map(([x, y], i) => Math.max(Math.abs(x - cells[i][0]), Math.abs(y - cells[i][1])) < ASSIGN_TOL
    && pyMod(cells[i][0] + cells[i][1], 2) === 0);
  return [cells, ok];
}

const count = (bools) => bools.reduce((n, b) => n + (b ? 1 : 0), 0);

function findHomography(src, dst, method = 0, thresh = 3) {
  const a = pointsMat(src, cv.CV_64FC2), b = pointsMat(dst, cv.CV_64FC2);
  const H = cv.findHomography(a, b, method, thresh);
  a.delete();
  b.delete();
  if (H.empty() || H.rows !== 3) { H.delete(); return null; }
  return matToArray(H);
}

function fitLattice(bl) {
  const centres = bl.map((b) => b.centre);
  const unit = [[0, 0], [1, 0], [1, 1], [0, 1]];
  const order = bl.map((_, i) => i).sort((i, j) => bl[j].area - bl[i].area);   // stable, like Python's sorted
  let best = null;
  for (const k of order.slice(0, 10)) {
    const src = pointsMat(bl[k].corners, cv.CV_32FC2), dst = pointsMat(unit, cv.CV_32FC2);
    let H = matToArray(cv.getPerspectiveTransform(src, dst));
    src.delete();
    dst.delete();
    for (let it = 0; it < 4; it++) {
      const [cells, ok] = assign(H, centres);
      if (count(ok) < 4) break;
      const H2 = findHomography(centres.filter((_, i) => ok[i]), cells.filter((_, i) => ok[i]).map(([x, y]) => [x + 0.5, y + 0.5]), 0);
      if (!H2) break;
      H = H2;
    }
    const [cells, ok] = assign(H, centres);
    if (!best || count(ok) > count(best.ok)) best = { H, cells, ok };
  }
  if (!best || count(best.ok) < MIN_BLOBS) return null;
  return best;
}

/** Image corners of inlier blobs paired with their integer lattice corners. */
function cornerPairs(H, bl, cells, ok) {
  const img = [], lat = [];
  bl.forEach((b, i) => {
    if (!ok[i]) return;
    const p = project(H, b.corners);
    p.forEach(([x, y], j) => {
      const t = [npRound(x), npRound(y)];
      const near = Math.max(Math.abs(x - t[0]), Math.abs(y - t[1])) < 0.35;
      const inside = t[0] >= cells[i][0] && t[1] >= cells[i][1] && t[0] <= cells[i][0] + 1 && t[1] <= cells[i][1] + 1;
      if (near && inside) { img.push(b.corners[j]); lat.push(t); }
    });
  });
  return [img, lat];
}

/** Turn/flip the lattice so u grows to the image right and v grows up the image (away from you). */
function canonical(H, [w, h]) {
  const p = project(H, [[w / 2, h / 2], [w / 2 + 20, h / 2], [w / 2, h / 2 - 20]]);
  const right = [p[1][0] - p[0][0], p[1][1] - p[0][1]], up = [p[2][0] - p[0][0], p[2][1] - p[0][1]];
  const norm = (x) => Math.hypot(x[0], x[1]);
  if (!p.flat().every(Number.isFinite) || !(norm(right) > 1e-9) || !(norm(up) > 1e-9)) throw new Error("degenerate lattice");
  let best = null, bestScore = -Infinity;
  for (const swap of [false, true]) for (const su of [1, -1]) for (const sv of [1, -1]) {
    const S = swap ? [[0, 1], [1, 0]] : [[1, 0], [0, 1]];
    const A = [[su * S[0][0], su * S[0][1]], [sv * S[1][0], sv * S[1][1]]];
    const mv = (x) => [A[0][0] * x[0] + A[0][1] * x[1], A[1][0] * x[0] + A[1][1] * x[1]];
    const r = mv(right), q = mv(up);
    const score = r[0] / norm(r) + q[1] / norm(q);
    if (score > bestScore) { best = A; bestScore = score; }
  }
  // Flipping an axis moves cell centres from i + 0.5 to -i - 0.5; shift by one to keep dark-square parity.
  const T = [best[0][0], best[0][1], best[0][0] + best[0][1] < 0 ? 1 : 0,
             best[1][0], best[1][1], best[1][0] + best[1][1] < 0 ? 1 : 0,
             0, 0, 1];
  return mul3(T, H);
}

/** Green fraction and in-frame flag for lattice cells [lo, hi)^2, as [v - lo][u - lo]. */
function greenCells(H, mask, lo, hi) {
  const n = hi - lo;
  const s = [0, 1, 2, 3, 4].map((i) => (i + 0.5) / 5 * 0.7 + 0.15);
  const Hinv = inv3(H);
  const w = mask.cols, h = mask.rows, data = mask.data;
  const green = [], inframe = [];
  for (let iv = 0; iv < n; iv++) {
    green.push(new Array(n));
    inframe.push(new Array(n));
    for (let iu = 0; iu < n; iu++) {
      const pts = [];
      for (const sv of s) for (const su of s) pts.push([lo + iu + su, lo + iv + sv]);
      let inside = 0, g = 0;
      for (const [px, py] of project(Hinv, pts)) {
        const x = npRound(px), y = npRound(py);
        if (x >= 0 && x < w && y >= 0 && y < h) {
          inside++;
          if (data[y * w + x] > 0) g++;
        }
      }
      green[iv][iu] = inside > 0 ? g / Math.max(inside, 1) : 0;
      inframe[iv][iu] = inside / 25 > 0.6;
    }
  }
  return [green, inframe];
}

/** Offset (ou, ov) of the 8x8 board in lattice cells, chosen by which cells look green. */
function pickWindow(H, mask, centreCell) {
  const lo = Math.trunc(Math.min(...centreCell)) - 12;
  const hi = Math.trunc(Math.max(...centreCell)) + 13;
  const [green, inframe] = greenCells(H, mask, lo, hi);
  const n = hi - lo;
  const dark = (r, c) => pyMod(lo + c + lo + r, 2) === 0;
  const seen = (r, c) => inframe[r][c] && green[r][c] > 0.35;
  const missing = (r, c) => inframe[r][c] && green[r][c] < 0.1;
  let seenTotal = 0;
  for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) if (seen(r, c)) seenTotal++;
  let best = null, bestScore = -Infinity;
  for (let ov = 0; ov < n - 7; ov++) {
    for (let ou = 0; ou < n - 7; ou++) {
      if (pyMod(lo + ou + lo + ov, 2) !== 0) continue;     // near-left square is always dark
      let inGreen = 0, inMissing = 0, inSeen = 0;
      for (let r = ov; r < ov + 8; r++) for (let c = ou; c < ou + 8; c++) {
        if (seen(r, c)) { inSeen++; if (dark(r, c)) inGreen++; }
        if (missing(r, c) && dark(r, c)) inMissing++;
      }
      const score = inGreen - inMissing - (seenTotal - inSeen);
      if (score > bestScore) { best = [lo + ou, lo + ov]; bestScore = score; }
    }
  }
  const [ou, ov] = best;
  let winGreen = 0;
  const winInframe = [];
  for (let v = 0; v < 8; v++) {
    winInframe.push([]);
    for (let u = 0; u < 8; u++) {
      const r = ov - lo + v, c = ou - lo + u;
      if (seen(r, c) && dark(r, c)) winGreen++;
      winInframe[v].push(inframe[r][c]);
    }
  }
  return [best, winGreen, winInframe];
}

/** The board in this frame (a BGR Mat), or null. */
export function findBoard(image) {
  try {
    return findBoardInner(image);
  } catch (err) {
    // Degenerate geometry (a blob misread as the lattice) or an OpenCV error is no board.
    if (err instanceof TypeError || err instanceof ReferenceError) throw err;
    return null;
  }
}

function findBoardInner(image) {
  const w = image.cols, h = image.rows;
  const mask = greenMask(image);
  try {
    const bl = blobs(mask);
    if (bl.length < MIN_BLOBS) return null;
    const fit = fitLattice(bl);
    if (!fit) return null;
    let H = canonical(fit.H, [w, h]);
    const centres = bl.map((b) => b.centre);
    let [cells, ok] = assign(H, centres);
    if (count(ok) < MIN_BLOBS) return null;

    const okCells = cells.filter((_, i) => ok[i]);
    const centreCell = [median(okCells.map((c) => c[0])), median(okCells.map((c) => c[1]))];
    const [offset, green, inframe] = pickWindow(H, mask, centreCell);
    H = mul3([1, 0, -offset[0], 0, 1, -offset[1], 0, 0, 1], H);
    cells = cells.map(([x, y]) => [x - offset[0], y - offset[1]]);
    const inBoard = ok.map((o, i) => o && cells[i][0] >= 0 && cells[i][1] >= 0 && cells[i][0] <= 7 && cells[i][1] <= 7);

    // Refit on the corners of the green squares inside the board, then model the bend.
    let [img, lat] = cornerPairs(H, bl, cells, inBoard);
    if (img.length >= 12) {
      const H2 = findHomography(img, lat, cv.RANSAC, 3.0);
      if (H2) {
        H = H2;
        [img, lat] = cornerPairs(H, bl, cells, inBoard);
      }
    }
    let coef = [[0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0]];
    let rms = 99;
    if (img.length >= 12) {
      const base = project(inv3(H), lat);
      const resid = img.map((p, i) => [p[0] - base[i][0], p[1] - base[i][1]]);
      const A = lat.map(quadTerms);
      if (img.length >= 30) {
        // Ridge keeps the bend small where few corners are seen.
        const AtA = Array.from({ length: 6 }, (_, i) => Array.from({ length: 6 }, (_, j) => A.reduce((s, r) => s + r[i] * r[j], 0)));
        for (let i = 1; i < 6; i++) AtA[i][i] += 1e-2 * img.length;
        const AtR = Array.from({ length: 6 }, (_, i) => [0, 1].map((d) => A.reduce((s, r, k) => s + r[i] * resid[k][d], 0)));
        const sol = solve(AtA, AtR);                  // (6, 2)
        coef = [sol.map((r) => r[0]), sol.map((r) => r[1])];
      }
      let sq = 0;
      A.forEach((t, k) => {
        let dx = resid[k][0], dy = resid[k][1];
        for (let j = 0; j < 6; j++) { dx -= t[j] * coef[0][j]; dy -= t[j] * coef[1][j]; }
        sq += dx * dx + dy * dy;
      });
      rms = Math.sqrt(sq / A.length);
    }

    const board = new BoardFit(H, coef, 0, inframe, green, rms, [w, h]);
    // Re-check visibility with the corrected mapping.
    const centresUV = [];
    for (let v = 0; v < 8; v++) for (let u = 0; u < 8; u++) centresUV.push([u + 0.5, v + 0.5]);
    const p = board.toImage(centresUV);
    board.visible = inframe.map((row, v) => row.map((inf, u) => {
      const [x, y] = p[v * 8 + u];
      return inf && x >= 0 && x < w && y >= 0 && y < h;
    }));
    let darkVisible = 0;
    for (let v = 0; v < 8; v++) for (let u = 0; u < 8; u++) if ((u + v) % 2 === 0 && board.visible[v][u]) darkVisible++;
    const seen = green / Math.max(darkVisible, 1);
    const rel = rms / Math.max(board.cellHeights(), 1);     // corner error as a fraction of a square
    const clip = (x) => Math.min(Math.max(x, 0), 1);
    board.quality = clip(seen) * clip(1.2 - 2 * rel);
    return board;
  } finally {
    mask.delete();
  }
}

// ---------- square features ----------

/** Values at the q-th percentile (NumPy's linear method) of 8-bit-quantised samples. */
function percentile(counts, total, q, valueOf) {
  const pos = q / 100 * (total - 1);
  const lo = Math.floor(pos), hi = Math.ceil(pos);
  let seen = 0, vlo = null, vhi = null;
  for (let i = 0; i < counts.length; i++) {
    seen += counts[i];
    if (vlo === null && seen > lo) vlo = valueOf(i);
    if (vhi === null && seen > hi) { vhi = valueOf(i); break; }
  }
  return vlo + (vhi - vlo) * (pos - lo);
}

/** Per square, view order v * 8 + u: {feats (64 x 25), visible (64)}. */
export function squareFeatures(warped, valid) {
  const px = CELL_PX, size = 8 * px, n = size * size;
  const lab = new cv.Mat(), gray = new cv.Mat(), edges = new cv.Mat(), hsv = new cv.Mat();
  cv.cvtColor(warped, lab, cv.COLOR_BGR2Lab);
  cv.cvtColor(warped, gray, cv.COLOR_BGR2GRAY);
  cv.Canny(gray, edges, 60, 140);
  cv.cvtColor(warped, hsv, cv.COLOR_BGR2HSV);
  const L = new Float32Array(n), A = new Float32Array(n), B = new Float32Array(n);
  const E = new Uint8Array(n), O = new Uint8Array(n), G = new Uint8Array(n);
  const counts = new Array(256).fill(0);
  let nValid = 0;
  const scale = Math.fround(100 / 255);
  for (let i = 0; i < n; i++) {
    const l8 = lab.data[3 * i];
    L[i] = Math.fround(l8 * scale);
    A[i] = lab.data[3 * i + 1] - 128;
    B[i] = lab.data[3 * i + 2] - 128;
    E[i] = edges.data[i] > 0 ? 1 : 0;
    const hh = hsv.data[3 * i], ss = hsv.data[3 * i + 1], vv = hsv.data[3 * i + 2];
    O[i] = ss > 140 && vv > 60 && !(hh >= 35 && hh <= 95) ? 1 : 0;      // sleeves, bright objects
    G[i] = Math.abs(A[i] + 12) < 9 && L[i] > 25 && L[i] < 75 ? 1 : 0;   // the board's muted green
    if (valid[i]) { counts[l8]++; nValid++; }
  }
  [lab, gray, edges, hsv].forEach((m) => m.delete());
  // Brightness cut-offs follow the room's light: the light squares set the reference.
  const ref = nValid ? percentile(counts, nValid, 75, (v) => Math.fround(v * scale)) : 90;
  const darkCut = 0.4 * ref, brightCut = 0.88 * ref;

  const region = (u, v, r) => {
    const y0 = (7 - v) * px, x0 = u * px;
    return [y0 + Math.trunc(r[2] * px), y0 + Math.trunc(r[3] * px), x0 + Math.trunc(r[0] * px), x0 + Math.trunc(r[1] * px)];
  };
  const stats = (u, v, r) => {
    if (v < 0 || v > 7) return [0, 0, 0, 0, 0, 0, 0, 0];
    const [ya, yb, xa, xb] = region(u, v, r);
    let k = 0, sL = 0, sA = 0, sB = 0, dark = 0, bright = 0, g = 0, e = 0;
    for (let y = ya; y < yb; y++) for (let x = xa; x < xb; x++) {
      const i = y * size + x;
      k++; sL += L[i]; sA += A[i]; sB += B[i];
      if (L[i] < darkCut) dark++;
      if (L[i] > brightCut) bright++;
      g += G[i]; e += E[i];
    }
    const mean = sL / k;
    let varL = 0;
    for (let y = ya; y < yb; y++) for (let x = xa; x < xb; x++) varL += (L[y * size + x] - mean) ** 2;
    return [mean, Math.sqrt(varL / k), sA / k, sB / k, dark / k, bright / k, g / k, e / k];
  };
  const fraction = (arr, [ya, yb, xa, xb]) => {
    let s = 0;
    for (let y = ya; y < yb; y++) for (let x = xa; x < xb; x++) s += arr[y * size + x];
    return s / ((yb - ya) * (xb - xa));
  };

  const feats = [], visible = [];
  for (let v = 0; v < 8; v++) {
    for (let u = 0; u < 8; u++) {
      feats.push([(u + v) % 2 === 0 ? 1 : 0, ...stats(u, v, BASE), ...stats(u, v, BODY), ...stats(u, v - 1, BASE)]);
      visible.push(fraction(valid, region(u, v, [0, 1, 0, 1])) >= MIN_VALID && fraction(O, region(u, v, BASE)) <= MAX_OCCLUDED);
    }
  }
  return { feats, visible };
}

// ---------- classifier ----------

/**
 * Multinomial logistic regression on standardised features, L2 penalty with C = 0.5
 * (the same model as scikit-learn's LogisticRegression(C=0.5)), fitted by Newton's method.
 */
export class SquareModel {
  constructor() {
    this.data = [[], [], []];        // per class, newest last, at most BUFFER_PER_CLASS
    this.W = null;                   // 3 x (F + 1): weights then intercept
  }

  get ready() { return this.W !== null; }

  add(feats, labels, mask = null) {
    feats.forEach((f, i) => {
      if (mask && !mask[i]) return;
      const d = this.data[labels[i]];
      d.push(f);
      if (d.length > BUFFER_PER_CLASS) d.shift();
    });
  }

  fit() {
    if (this.data.some((d) => d.length < 4)) return false;
    const X = [], y = [];
    this.data.forEach((d, c) => d.forEach((f) => { X.push(f); y.push(c); }));
    const F = X[0].length, N = X.length;
    // Standardise (population std; constant columns keep scale 1).
    const mean = new Array(F).fill(0), std = new Array(F).fill(0);
    for (const r of X) r.forEach((x, j) => { mean[j] += x / N; });
    for (const r of X) r.forEach((x, j) => { std[j] += (x - mean[j]) ** 2 / N; });
    for (let j = 0; j < F; j++) std[j] = std[j] > 0 ? Math.sqrt(std[j]) : 1;
    const Z = X.map((r) => [...r.map((x, j) => (x - mean[j]) / std[j]), 1]);
    const D = F + 1, K = 3, P = K * D, C = 0.5;

    const objective = (W) => {
      let f = 0;
      for (let k = 0; k < K; k++) for (let j = 0; j < F; j++) f += 0.5 * W[k][j] ** 2;
      for (let n = 0; n < N; n++) {
        const s = W.map((w) => w.reduce((acc, wj, j) => acc + wj * Z[n][j], 0));
        const mx = Math.max(...s);
        f += C * (mx + Math.log(s.reduce((acc, x) => acc + Math.exp(x - mx), 0)) - s[y[n]]);
      }
      return f;
    };
    // Warm start from the last fit: the optimum is unique, so this only saves iterations.
    let W = this.W && this.W[0].length === D ? this.W.map((r) => [...r]) : [new Array(D).fill(0), new Array(D).fill(0), new Array(D).fill(0)];
    let fW = objective(W);
    for (let it = 0; it < 50; it++) {
      // Objective: 0.5 * |W without intercepts|^2 + C * sum of log-losses.
      const g = new Array(P).fill(0);
      const Hm = Array.from({ length: P }, () => new Array(P).fill(0));
      for (let n = 0; n < N; n++) {
        const z = Z[n];
        const s = W.map((w) => w.reduce((acc, wj, j) => acc + wj * z[j], 0));
        const mx = Math.max(...s);
        const e = s.map((x) => Math.exp(x - mx));
        const tot = e[0] + e[1] + e[2];
        const p = e.map((x) => x / tot);
        for (let k = 0; k < K; k++) {
          const r = C * (p[k] - (y[n] === k ? 1 : 0));
          for (let j = 0; j < D; j++) g[k * D + j] += r * z[j];
          for (let l = 0; l < K; l++) {
            const hk = C * p[k] * ((k === l ? 1 : 0) - p[l]);
            if (!hk) continue;
            for (let i = 0; i < D; i++) {
              const zi = hk * z[i];
              const row = Hm[k * D + i];
              for (let j = 0; j < D; j++) row[l * D + j] += zi * z[j];
            }
          }
        }
      }
      for (let k = 0; k < K; k++) for (let j = 0; j < D; j++) {
        const idx = k * D + j;
        if (j < F) { g[idx] += W[k][j]; Hm[idx][idx] += 1; } else Hm[idx][idx] += 1e-8;
      }
      const step = solve(Hm, g.map((x) => [x])).map((r) => r[0]);
      // Newton step, halved until the objective goes down.
      let t = 1, next = null, fNext = Infinity;
      for (let tries = 0; tries < 20; tries++, t /= 2) {
        next = W.map((w, k) => w.map((wj, j) => wj - t * step[k * D + j]));
        fNext = objective(next);
        if (fNext <= fW) break;
      }
      if (!(fNext <= fW)) break;
      const change = Math.max(...step.map(Math.abs)) * t;
      W = next;
      fW = fNext;
      if (change < 1e-7) break;
    }
    this.W = W;
    this.mean = mean;
    this.std = std;
    return true;
  }

  /** 64 rows of [empty, white, black]. */
  predict(feats) {
    return feats.map((r) => {
      const z = [...r.map((x, j) => (x - this.mean[j]) / this.std[j]), 1];
      const s = this.W.map((w) => w.reduce((acc, wj, j) => acc + wj * z[j], 0));
      const mx = Math.max(...s);
      const e = s.map((x) => Math.exp(x - mx));
      const tot = e[0] + e[1] + e[2];
      return e.map((x) => x / tot);
    });
  }
}

// ---------- the reader ----------

/** View cell (u from the left, v from the near side) -> real square (0 = a1). */
export function viewToSquare(u, v, color) {
  return color === "w" ? v * 8 + u : (7 - v) * 8 + (7 - u);
}

const START_ROWS = [1, 1, 0, 0, 0, 0, 2, 2];     // starting position by view row: empty, mine, theirs

function occupancyFromFen(fen) {
  const occ = new Array(64).fill(EMPTY);
  fen.split(" ")[0].split("/").forEach((row, i) => {
    let f = 0;
    for (const ch of row) {
      if (/\d/.test(ch)) { f += Number(ch); continue; }
      occ[(7 - i) * 8 + f] = ch === ch.toUpperCase() ? WHITE : BLACK;
      f++;
    }
  });
  return occ;
}

export class BoardReader {
  constructor() { this.reset(); }

  reset() {
    this.model = new SquareModel();
    this.userColor = null;          // "w" | "b"
    this.lastFeats = null;
    this.lastVisible = null;
    this.lastBoard = null;
    this.lastSize = null;
    this.relearn = null;            // set by Recalibrate: learn from this position, not the start
    this.why("waiting for the starting position", "Look at the starting position");
  }

  /** `note` is for the details panel; `hint` is the short advice shown big. */
  why(note, hint) {
    this.note = note;
    this.hint = hint;
  }

  /**
   * One frame as a BGR cv.Mat (it is not freed here).
   * Returns null (no usable board) or {probs (64 x 3, a1 first), confidence, userColor}.
   */
  read(bgr) {
    let image = bgr;
    const scale = MAX_SIDE / Math.max(bgr.rows, bgr.cols);
    if (scale < 1) {
      image = new cv.Mat();
      cv.resize(bgr, image, new cv.Size(npRound(bgr.cols * scale), npRound(bgr.rows * scale)), 0, 0, cv.INTER_AREA);
    }
    try {
      return this.readScaled(image);
    } finally {
      if (image !== bgr) image.delete();
    }
  }

  readScaled(image) {
    this.lastSize = [image.cols, image.rows];
    const board = findBoard(image);
    if (!board || board.quality < TUNING.minBoardQuality) {
      this.lastBoard = null;
      if (!board) this.why("no board in view", "Look at the board");
      else this.why(`board unclear (quality ${board.quality.toFixed(2)})`, "Sit closer and hold still");
      return null;
    }
    const { warped, valid } = board.warp(image);
    let feats, visible;
    try {
      ({ feats, visible } = squareFeatures(warped, valid));
    } finally {
      warped.delete();
    }
    const nVisible = count(visible);
    if (nVisible < TUNING.minVisibleSquares) {
      this.lastBoard = board;          // still drawn, to show what is cut off
      this.why(`only ${nVisible} squares in view`, "Get the whole board in view");
      return null;
    }
    this.lastFeats = feats;
    this.lastVisible = visible;
    this.lastBoard = board;
    if (!this.model.ready) {
      const learned = this.relearn ? this.relearnFrom(feats, visible) : this.bootstrap(feats, visible);
      if (!learned) return null;
    } else {
      this.why(`reading the board (${nVisible} squares in view)`, null);
    }
    const view = this.model.predict(feats);
    visible.forEach((vis, i) => { if (!vis) view[i] = [1 / 3, 1 / 3, 1 / 3]; });
    const probs = new Array(64);
    for (let v = 0; v < 8; v++) for (let u = 0; u < 8; u++) probs[viewToSquare(u, v, this.userColor)] = view[v * 8 + u];
    let conf = 0;
    visible.forEach((vis, i) => { if (vis) conf += Math.max(...view[i]); });
    return { probs, confidence: conf / nVisible, userColor: this.userColor };
  }

  /** Fit the classifier if this frame looks like the starting position. */
  bootstrap(feats, visible) {
    const nVisible = count(visible);
    if (nVisible < 56) {
      this.why(`starting position: only ${nVisible} squares in view`, "Get the whole board in view");
      return false;
    }
    // Judge emptiness on the green squares only: any piece hides green, while a cream
    // piece on a cream square looks almost like an empty one. The third row from you is
    // empty but your pieces lean over it, so judge emptiness on the three rows after it.
    const a = [], b = [];
    feats.forEach((f, i) => {
      const row = i >> 3;
      if (!visible[i] || f[0] <= 0.5) return;
      const plain = f[F_BASE_GREEN] - 2 * f[F_BASE_EDGE];
      if (START_ROWS[row] > 0) b.push(plain);
      else if (row >= 3) a.push(plain);
    });
    if (a.length < 8 || b.length < 10) {
      this.why("starting position: too few squares in view", "Get the whole board in view");
      return false;
    }
    let above = 0;
    for (const x of a) for (const yv of b) if (x > yv) above++;
    const separation = above / (a.length * b.length);      // rank AUC: empty looks plainer
    const mean = (xs) => xs.reduce((s, x) => s + x, 0) / xs.length;
    const contrast = mean(a) - mean(b);
    if (separation < 0.92 || contrast < 0.4) {
      this.why(`not the starting position (separation ${separation.toFixed(2)}, contrast ${contrast.toFixed(2)})`,
        "Set up the starting position");
      return false;
    }
    const darkness = feats.map((f) => f[F_BASE_DARK] + f[F_BODY_DARK]);
    const near = mean(darkness.filter((_, i) => visible[i] && START_ROWS[i >> 3] === 1));
    const far = mean(darkness.filter((_, i) => visible[i] && START_ROWS[i >> 3] === 2));
    if (Math.abs(near - far) < 0.3) {
      this.why(`can't tell which side is yours (near ${near.toFixed(2)}, far ${far.toFixed(2)})`, "Look down the board from your side");
      return false;
    }
    this.userColor = near < far ? "w" : "b";
    const [mine, theirs] = this.userColor === "w" ? [WHITE, BLACK] : [BLACK, WHITE];
    const labels = feats.map((_, i) => [EMPTY, mine, theirs][START_ROWS[i >> 3]]);
    this.model.add(feats, labels, visible);
    const ok = this.model.fit();
    this.why(`calibrated: you are ${this.userColor === "w" ? "White" : "Black"}`, null);
    return ok;
  }

  /** After Recalibrate: label this frame with the confirmed position and refit. */
  relearnFrom(feats, visible) {
    const nVisible = count(visible);
    if (nVisible < 56) {
      this.why(`recalibrating: only ${nVisible} squares in view`, "Get the whole board in view");
      return false;
    }
    const fen = this.relearn;
    this.relearn = null;
    this.learn(fen);
    if (!this.model.ready) {
      this.relearn = fen;
      return false;
    }
    this.why("recalibrated from the current position", null);
    return true;
  }

  /** GameSession listener: new games reset, confirmed positions teach the classifier. */
  onEvent(kind, data) {
    if (kind === "new_game" || kind === "end") { this.reset(); return; }
    if (kind === "recalibrate") {
      // Forget what the squares look like (the light changed, the camera moved). Mid-game,
      // relearn from the confirmed position; before the lock, from the starting position.
      const color = this.userColor, fen = data.fen;
      this.reset();
      if (fen && color) {
        this.userColor = color;
        this.relearn = fen;
        this.why("recalibrating from the current position", "Hold still on the board");
      }
      return;
    }
    if (kind === "lock") {
      // The page may override the detected side: relearn with the other orientation.
      if (this.userColor && data.userColor !== this.userColor) this.reset();
      this.userColor = data.userColor;
    }
    if ((kind === "lock" || kind === "move" || kind === "undo") && data.fen && this.lastFeats) this.learn(data.fen);
  }

  learn(fen) {
    if (!this.userColor || !this.lastFeats) return;
    const occ = occupancyFromFen(fen);
    const labels = [];
    for (let v = 0; v < 8; v++) for (let u = 0; u < 8; u++) labels.push(occ[viewToSquare(u, v, this.userColor)]);
    this.model.add(this.lastFeats, labels, this.lastVisible);
    this.model.fit();
  }

  /** Board grid lines in source-frame pixels, for drawing over the camera: [{pts, near}]. */
  grid(frameWidth) {
    const board = this.lastBoard, size = this.lastSize;
    if (!board || !size) return [];
    const k = frameWidth / size[0];
    const lines = [];
    const t = Array.from({ length: 33 }, (_, i) => i / 4);
    for (let i = 0; i <= 8; i++) {
      lines.push({ pts: board.toImage(t.map((x) => [i, x])).map(([x, y]) => [x * k, y * k]), near: false });
      lines.push({ pts: board.toImage(t.map((x) => [x, i])).map(([x, y]) => [x * k, y * k]), near: i === 0 });
    }
    return lines;
  }
}
