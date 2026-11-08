"""Find the green-and-white board in a frame, without markers.

1. Threshold the board's green and find the green squares as blobs.
2. Fit a lattice: from one blob's four corners, guess a homography, then
   refit it from every blob that lands on a dark-square cell.
3. Turn the lattice so the near side of the board is at the bottom, and
   pick the 8x8 window that best explains which cells are green.
4. Correct for a wavy (bent) board with a smooth quadratic fit of the
   remaining corner errors.

Coordinates: "view" cells (u, v) with u = file from the left as seen,
v = row from the near side, 0..7. Which real square that is depends on the
side the user plays, which the reader decides at calibration.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

# The board's green is muted: hue ~63 (OpenCV 0-180), saturation ~50.
GREEN_LO = np.array([38, 25, 35], np.uint8)
GREEN_HI = np.array([95, 255, 235], np.uint8)
MIN_BLOBS = 6              # fewer green squares than this = no board
ASSIGN_TOL = 0.3           # lattice units: how close a blob centre must land to a cell centre
CELL_PX = 64               # warped board: pixels per square


@dataclass
class BoardFit:
    H: np.ndarray            # image -> view lattice (homography), cells are unit squares
    coef: np.ndarray         # (2, 6) quadratic correction in image pixels, by lattice position
    quality: float           # 0..1
    visible: np.ndarray      # (8, 8) bool [v, u]: cell inside the frame
    green_cells: int         # dark squares actually seen green
    rms: float               # corner fit error, pixels
    image_size: tuple[int, int]

    def to_image(self, uv: np.ndarray) -> np.ndarray:
        """View lattice points (N, 2) -> image pixels (N, 2)."""
        uv = np.asarray(uv, np.float64).reshape(-1, 2)
        pts = cv2.perspectiveTransform(uv.reshape(-1, 1, 2), np.linalg.inv(self.H)).reshape(-1, 2)
        return pts + _quad_terms(uv) @ self.coef.T

    def warp(self, image: np.ndarray, px: int = CELL_PX) -> tuple[np.ndarray, np.ndarray]:
        """Top-down board, far side at the top, near-left cell at the bottom left.

        Returns (warped BGR image, valid mask): pixels outside the frame are not valid.
        """
        size = 8 * px
        xs = (np.arange(size) + 0.5) / px
        u, row = np.meshgrid(xs, xs)
        v = 8 - row
        pts = self.to_image(np.stack([u.ravel(), v.ravel()], 1)).astype(np.float32)
        mx = pts[:, 0].reshape(size, size)
        my = pts[:, 1].reshape(size, size)
        warped = cv2.remap(image, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
        h, w = image.shape[:2]
        valid = (mx >= 0) & (mx <= w - 1) & (my >= 0) & (my <= h - 1)
        return warped, valid

    def cell_heights(self) -> float:
        """Median height in pixels of the visible squares (for the 'sit closer' warning)."""
        out = []
        for v in range(8):
            for u in range(8):
                if self.visible[v, u]:
                    a, b = self.to_image(np.array([[u + 0.5, v], [u + 0.5, v + 1]]))
                    out.append(abs(a[1] - b[1]))
        return float(np.median(out)) if out else 0.0


def green_mask(image: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, GREEN_LO, GREEN_HI)
    return cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))


@dataclass
class _Blob:
    centre: np.ndarray       # (2,)
    corners: np.ndarray      # (4, 2)
    area: float


def _blobs(mask: np.ndarray) -> list[_Blob]:
    # Diagonal neighbours touch at a corner; a light erosion separates them.
    eroded = cv2.erode(mask, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(eroded, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    blobs = []
    for c in contours:
        area = cv2.contourArea(c)
        if area < 30:
            continue
        hull = cv2.convexHull(c)
        solidity = area / max(cv2.contourArea(hull), 1)
        if solidity < 0.75:
            continue
        approx = cv2.approxPolyDP(hull, 0.08 * cv2.arcLength(hull, True), True).reshape(-1, 2)
        corners = approx if len(approx) == 4 else cv2.boxPoints(cv2.minAreaRect(c))
        m = cv2.moments(c)
        centre = np.array([m["m10"] / m["m00"], m["m01"] / m["m00"]])
        blobs.append(_Blob(centre, _order(np.asarray(corners, np.float64), centre), area))
    if not blobs:
        return []
    med = np.median([b.area for b in blobs])
    return [b for b in blobs if 0.2 * med < b.area < 4 * med]


def _order(corners: np.ndarray, centre: np.ndarray) -> np.ndarray:
    ang = np.arctan2(corners[:, 1] - centre[1], corners[:, 0] - centre[0])
    return corners[np.argsort(ang)]


def _assign(H: np.ndarray, centres: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Map blob centres through H; return (cell indices (N, 2), inlier mask) for dark-parity cells."""
    p = cv2.perspectiveTransform(centres.reshape(-1, 1, 2), H).reshape(-1, 2) - 0.5
    cells = np.round(p)
    ok = (np.abs(p - cells).max(axis=1) < ASSIGN_TOL) & ((cells.sum(axis=1) % 2) == 0)
    return cells.astype(int), ok


def _fit_lattice(blobs: list[_Blob]) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """Homography image -> lattice with green squares on cells where i + j is even."""
    centres = np.array([b.centre for b in blobs])
    unit = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], np.float64)
    order = sorted(range(len(blobs)), key=lambda i: -blobs[i].area)
    best = None
    for k in order[:10]:
        H = cv2.getPerspectiveTransform(blobs[k].corners.astype(np.float32), unit.astype(np.float32))
        for _ in range(4):
            cells, ok = _assign(H, centres)
            if ok.sum() < 4:
                break
            H2, _ = cv2.findHomography(centres[ok], cells[ok] + 0.5, 0)
            if H2 is None:
                break
            H = H2
        cells, ok = _assign(H, centres)
        if best is None or ok.sum() > best[2].sum():
            best = (H, cells, ok)
    if best is None or best[2].sum() < MIN_BLOBS:
        return None
    return best


def _corner_pairs(H, blobs, cells, ok) -> tuple[np.ndarray, np.ndarray]:
    """Image corners of inlier blobs paired with their integer lattice corners."""
    img, lat = [], []
    for b, cell, good in zip(blobs, cells, ok):
        if not good:
            continue
        p = cv2.perspectiveTransform(b.corners.reshape(-1, 1, 2), H).reshape(-1, 2)
        target = np.round(p)
        near = np.abs(p - target).max(axis=1) < 0.35
        inside = (target >= cell).all(axis=1) & (target <= cell + 1).all(axis=1)
        for c, t, n, i in zip(b.corners, target, near, inside):
            if n and i:
                img.append(c)
                lat.append(t)
    return np.array(img, np.float64).reshape(-1, 2), np.array(lat, np.float64).reshape(-1, 2)


def _canonical(H: np.ndarray, image_size: tuple[int, int]) -> np.ndarray:
    """Turn/flip the lattice so u grows to the image right and v grows up the image (away from you)."""
    w, h = image_size
    c = np.array([[[w / 2, h / 2]], [[w / 2 + 20, h / 2]], [[w / 2, h / 2 - 20]]], np.float64)
    p = cv2.perspectiveTransform(c, H).reshape(-1, 2)
    right, up = p[1] - p[0], p[2] - p[0]
    if not (np.isfinite(p).all() and np.linalg.norm(right) > 1e-9 and np.linalg.norm(up) > 1e-9):
        raise ValueError("degenerate lattice")
    best, best_score = None, -np.inf
    for swap in (False, True):
        for su in (1, -1):
            for sv in (1, -1):
                A = np.array([[0, 1], [1, 0]] if swap else [[1, 0], [0, 1]], np.float64)
                A = np.diag([su, sv]) @ A
                r, q = A @ right, A @ up
                score = r[0] / np.linalg.norm(r) + q[1] / np.linalg.norm(q)
                if score > best_score:
                    best, best_score = A, score
    T = np.eye(3)
    T[:2, :2] = best
    # Flipping an axis moves cell centres from i + 0.5 to -i - 0.5; shift by one to keep dark-square parity.
    T[:2, 2] = (best.sum(axis=1) < 0).astype(float)
    return T @ H


def _green_cells(H: np.ndarray, mask: np.ndarray, lo: int, hi: int):
    """Green fraction and in-frame flag for lattice cells [lo, hi) x [lo, hi)."""
    n = hi - lo
    s = (np.arange(5) + 0.5) / 5 * 0.7 + 0.15               # sample points inside each cell
    su, sv = np.meshgrid(s, s)
    iu, iv = np.meshgrid(np.arange(lo, hi), np.arange(lo, hi))
    u = (iu[..., None, None] + su).reshape(-1)
    v = (iv[..., None, None] + sv).reshape(-1)
    pts = cv2.perspectiveTransform(np.stack([u, v], 1).reshape(-1, 1, 2), np.linalg.inv(H)).reshape(-1, 2)
    h, w = mask.shape
    x, y = np.round(pts[:, 0]).astype(int), np.round(pts[:, 1]).astype(int)
    inside = (x >= 0) & (x < w) & (y >= 0) & (y < h)
    g = np.zeros(len(x), bool)
    g[inside] = mask[y[inside], x[inside]] > 0
    inside = inside.reshape(n, n, -1)
    g = g.reshape(n, n, -1)
    frac_in = inside.mean(axis=2)
    green = np.where(frac_in > 0, g.sum(axis=2) / np.maximum(inside.sum(axis=2), 1), 0.0)
    return green, frac_in > 0.6        # [v, u] indexing


def _pick_window(H: np.ndarray, mask: np.ndarray, centre_cell: np.ndarray):
    """Offset (ou, ov) of the 8x8 board in lattice cells, chosen by which cells look green."""
    lo = int(min(centre_cell)) - 12
    hi = int(max(centre_cell)) + 13
    green, inframe = _green_cells(H, mask, lo, hi)
    n = hi - lo
    iu, iv = np.meshgrid(np.arange(lo, hi), np.arange(lo, hi))
    dark = ((iu + iv) % 2) == 0
    seen_green = inframe & (green > 0.35)
    missing = inframe & (green < 0.1)
    best, best_score = None, -np.inf
    for ov in range(n - 7):
        for ou in range(n - 7):
            if (iu[ov, ou] + iv[ov, ou]) % 2 != 0:     # near-left square is always dark
                continue
            win = np.zeros((n, n), bool)
            win[ov:ov + 8, ou:ou + 8] = True
            score = (
                (seen_green & dark & win).sum()
                - (missing & dark & win).sum()
                - (seen_green & ~win).sum()
            )
            if score > best_score:
                best, best_score = (iu[ov, ou], iv[ov, ou]), score
    ou, ov = best
    win_green = (seen_green & dark)[ov - lo:ov - lo + 8, ou - lo:ou - lo + 8].sum()
    win_inframe = inframe[ov - lo:ov - lo + 8, ou - lo:ou - lo + 8]
    return np.array([ou, ov]), int(win_green), win_inframe


def _quad_terms(uv: np.ndarray) -> np.ndarray:
    u, v = uv[:, 0] / 8, uv[:, 1] / 8
    return np.stack([np.ones_like(u), u, v, u * u, u * v, v * v], 1)


def find_board(image: np.ndarray) -> BoardFit | None:
    """The board in this frame, or None. Degenerate geometry (a blob misread as the lattice) is no board."""
    try:
        return _find_board(image)
    except (np.linalg.LinAlgError, cv2.error, ValueError):
        return None


def _find_board(image: np.ndarray) -> BoardFit | None:
    h, w = image.shape[:2]
    mask = green_mask(image)
    blobs = _blobs(mask)
    if len(blobs) < MIN_BLOBS:
        return None
    fit = _fit_lattice(blobs)
    if fit is None:
        return None
    H, cells, ok = fit
    H = _canonical(H, (w, h))
    cells, ok = _assign(H, np.array([b.centre for b in blobs]))
    if ok.sum() < MIN_BLOBS:
        return None

    offset, green_cells, inframe = _pick_window(H, mask, np.median(cells[ok], axis=0))
    shift = np.eye(3)
    shift[:2, 2] = -offset
    H = shift @ H
    cells = cells - offset
    in_board = ok & (cells >= 0).all(axis=1) & (cells <= 7).all(axis=1)

    # Refit on the corners of the green squares inside the board, then model the bend.
    img_pts, lat_pts = _corner_pairs(H, blobs, cells, in_board)
    if len(img_pts) >= 12:
        H2, _ = cv2.findHomography(img_pts, lat_pts, cv2.RANSAC, 3.0)
        if H2 is not None:
            H = H2
            img_pts, lat_pts = _corner_pairs(H, blobs, cells, in_board)
    coef = np.zeros((2, 6))
    rms = 99.0
    if len(img_pts) >= 12:
        base = cv2.perspectiveTransform(lat_pts.reshape(-1, 1, 2), np.linalg.inv(H)).reshape(-1, 2)
        resid = img_pts - base
        A = _quad_terms(lat_pts)
        if len(img_pts) >= 30:
            # Ridge keeps the bend small where few corners are seen.
            reg = 1e-2 * len(img_pts) * np.eye(6)
            reg[0, 0] = 0
            coef = np.linalg.solve(A.T @ A + reg, A.T @ resid).T
        rms = float(np.sqrt(((resid - A @ coef.T) ** 2).sum(axis=1).mean()))

    board = BoardFit(H, coef, 0.0, inframe, green_cells, rms, (w, h))
    # Re-check visibility with the corrected mapping.
    centres = np.array([[u + 0.5, v + 0.5] for v in range(8) for u in range(8)])
    p = board.to_image(centres)
    board.visible = ((p[:, 0] >= 0) & (p[:, 0] < w) & (p[:, 1] >= 0) & (p[:, 1] < h)).reshape(8, 8) & inframe
    dark_visible = sum(1 for v in range(8) for u in range(8) if (u + v) % 2 == 0 and board.visible[v, u])
    seen = green_cells / max(dark_visible, 1)
    rel = rms / max(board.cell_heights(), 1.0)      # corner error as a fraction of a square
    board.quality = float(np.clip(seen, 0, 1) * np.clip(1.2 - 2 * rel, 0, 1))
    return board
