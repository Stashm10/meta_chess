// Meta Chess dashboard: renders state from /ws and drives the control routes.

const $ = (id) => document.getElementById(id);
const GLYPHS = { K: "♚", Q: "♛", R: "♜", B: "♝", N: "♞", P: "♟" };
const CODES = ["e", "w", "b"];

let state = null;
let stableFrames = 3;
let fake = null;            // 64 ints, a1 first
let flipped = false;

// ---------- helpers ----------

function squareName(sq) { return "abcdefgh"[sq % 8] + (Math.floor(sq / 8) + 1); }
function isLight(sq) { return (Math.floor(sq / 8) + (sq % 8)) % 2 === 1; }

// Display order: rank 8..1 top to bottom for White, reversed for Black.
function displayOrder() {
  const order = [];
  for (let row = 0; row < 8; row++) {
    for (let col = 0; col < 8; col++) {
      const rank = flipped ? row : 7 - row;
      const file = flipped ? 7 - col : col;
      order.push(rank * 8 + file);
    }
  }
  return order;
}

function parseFen(fen) {
  const pieces = new Array(64).fill(null);
  const rows = fen.split(" ")[0].split("/");
  rows.forEach((row, i) => {
    let file = 0;
    for (const ch of row) {
      if (/\d/.test(ch)) { file += Number(ch); continue; }
      pieces[(7 - i) * 8 + file] = ch;
      file++;
    }
  });
  return pieces;
}

function startOccupancy() {
  return Array.from({ length: 64 }, (_, sq) => (sq < 16 ? 1 : sq >= 48 ? 2 : 0));
}

async function post(path, body) {
  const resp = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!resp.ok) throw new Error(`${path}: ${resp.status} ${await resp.text()}`);
  return resp.json();
}

// ---------- rendering ----------

function renderBoard() {
  const pieces = parseFen(state.fen);
  const order = displayOrder();
  const size = 100;
  let svg = `<svg viewBox="0 0 800 800" role="img" aria-label="Chess board">`;
  order.forEach((sq, i) => {
    const x = (i % 8) * size, y = Math.floor(i / 8) * size;
    svg += `<rect x="${x}" y="${y}" width="${size}" height="${size}" fill="var(${isLight(sq) ? "--light-sq" : "--dark-sq"})"/>`;
    if (i % 8 === 0) svg += `<text x="${x + 5}" y="${y + 18}" font-size="15" fill="var(${isLight(sq) ? "--dark-sq" : "--light-sq"})">${squareName(sq)[1]}</text>`;
    if (i >= 56) svg += `<text x="${x + size - 15}" y="${y + size - 6}" font-size="15" fill="var(${isLight(sq) ? "--dark-sq" : "--light-sq"})">${squareName(sq)[0]}</text>`;
    const p = pieces[sq];
    if (p) {
      const white = p === p.toUpperCase();
      svg += `<text x="${x + 50}" y="${y + 72}" font-size="70" text-anchor="middle"
        fill="${white ? "#fff" : "#111"}" stroke="${white ? "#222" : "#000"}" stroke-width="2">${GLYPHS[p.toUpperCase()]}</text>`;
    }
  });
  if (state.best_move_uci) {
    const center = (sq) => {
      const i = order.indexOf(sq);
      return [(i % 8) * size + 50, Math.floor(i / 8) * size + 50];
    };
    const from = center(squareToIndex(state.best_move_uci.slice(0, 2)));
    const to = center(squareToIndex(state.best_move_uci.slice(2, 4)));
    svg += `<defs><marker id="head" markerWidth="4" markerHeight="4" refX="2.2" refY="2" orient="auto">
      <path d="M0,0 L4,2 L0,4 z" fill="var(--arrow)"/></marker></defs>
      <line x1="${from[0]}" y1="${from[1]}" x2="${to[0]}" y2="${to[1]}" stroke="var(--arrow)"
        stroke-width="16" stroke-linecap="round" marker-end="url(#head)"/>`;
  }
  svg += "</svg>";
  $("board").innerHTML = svg;
}

function squareToIndex(name) { return "abcdefgh".indexOf(name[0]) + 8 * (Number(name[1]) - 1); }

function renderGrid(el, occ, compareTo) {
  el.innerHTML = "";
  displayOrder().forEach((sq) => {
    const cell = document.createElement("div");
    cell.className = `${isLight(sq) ? "l" : "d"} ${occ ? CODES[occ[sq]] : ""}`;
    if (compareTo && occ && compareTo[sq] !== occ[sq]) cell.classList.add("diff");
    cell.title = squareName(sq);
    cell.dataset.sq = sq;
    el.appendChild(cell);
  });
}

function renderMoves() {
  const ol = $("moves");
  ol.innerHTML = "";
  state.moves.forEach((san, i) => {
    if (i % 2 === 0) {
      const n = document.createElement("li");
      n.className = "n";
      n.textContent = `${i / 2 + 1}.`;
      ol.appendChild(n);
    }
    const li = document.createElement("li");
    li.textContent = san;
    if (i === state.moves.length - 1) li.className = "last";
    ol.appendChild(li);
  });
  if (state.moves.length % 2 === 1) ol.appendChild(document.createElement("li"));
  ol.scrollTop = ol.scrollHeight;
}

function render() {
  flipped = state.user_color === "Black";
  const status = $("status");
  status.textContent = state.status.replace("_", " ");
  status.dataset.status = state.status;
  $("color").textContent = state.user_color ? `You: ${state.user_color}` : "";
  $("visible").textContent = state.status === "idle" ? "" : state.board_visible ? "board in view" : "no board in view";
  $("fen").textContent = state.fen;
  $("best").textContent = state.best_move || "—";
  $("spoken").textContent = state.spoken ? `Last said: “${state.spoken}”` : "";
  $("confidence").textContent = state.confidence != null ? `confidence ${state.confidence.toFixed(2)}` : "";
  $("pgn").textContent = state.last_pgn_path ? `Saved ${state.last_pgn_path}` : "";
  renderBoard();
  renderMoves();
  renderGrid($("observed"), state.occupancy, state.expected_occupancy);
  if (!fake) fake = state.expected_occupancy.slice();
  renderGrid($("fake"), fake, state.expected_occupancy);
}

// ---------- live state ----------

function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.onopen = () => $("conn").classList.add("ok");
  ws.onmessage = (ev) => { state = JSON.parse(ev.data); render(); };
  ws.onclose = () => { $("conn").classList.remove("ok"); setTimeout(connect, 1000); };
}

// ---------- controls ----------

function wire(id, fn) {
  $(id).addEventListener("click", async () => {
    try { await fn(); } catch (err) { console.error(err); alert(err.message); }
  });
}

wire("btn-new", () => {
  fake = startOccupancy();
  const color = $("new-color").value;
  return post("/game/new", color ? { user_color: color } : null);
});
wire("btn-repeat", () => post("/speak/repeat"));
wire("btn-undo", () => post("/game/undo"));
wire("btn-recal", () => post("/game/recalibrate"));
wire("btn-end", async () => {
  const { pgn } = await post("/game/end");
  if (pgn) console.log(pgn);
});
wire("btn-test", () => post("/speak/test"));
wire("btn-load", () => { fake = state.expected_occupancy.slice(); render(); });
wire("btn-start", () => { fake = startOccupancy(); render(); });
wire("btn-noboard", () => post("/debug/no_board"));
wire("btn-send", () => {
  const p = fake.map((c) => [0, 1, 2].map((k) => (k === c ? 0.95 : 0.025)));
  return post("/debug/occupancy", { probs: p, repeat: stableFrames });
});
$("speak-on").addEventListener("change", async (ev) => {
  try { await post("/settings", { speak_on: ev.target.value }); } catch (err) { alert(err.message); }
});

$("fake").addEventListener("click", (ev) => {
  const sq = ev.target.dataset.sq;
  if (sq === undefined) return;
  fake[sq] = (fake[sq] + 1) % 3;
  render();
});

// ---------- capture ----------

let capture = null;
let lastFrames = -1;

function showSourceFields() {
  const kind = $("source").value;
  $("source-region").hidden = kind !== "region";
  $("source-file").hidden = kind !== "file";
}

function renderCapture() {
  const c = capture;
  const btn = $("btn-capture");
  btn.textContent = c.running ? "Stop capture" : "Start capture";
  btn.classList.toggle("primary", !c.running);
  if (c.running && c.source) { $("source").value = c.source; showSourceFields(); }
  $("record").checked = !!c.recording;

  const parts = [];
  if (c.running) parts.push(`${c.source} · ${c.fps_actual.toFixed(1)} fps`);
  else if (c.ended) parts.push("finished");
  if (c.frame_size) parts.push(`${c.frame_size[0]}×${c.frame_size[1]}`);
  if (c.frames) parts.push(`${c.frames} frames`);
  if (c.recording) parts.push(`recording ${c.recorded_frames} → ${c.recording}`);
  $("capture-status").textContent = parts.join(" · ");

  $("vision-note").textContent = c.running && c.vision ? `Vision: ${c.vision}` : "";

  $("capture-error").hidden = !c.error;
  $("capture-error").textContent = c.error || "";

  if (c.frames !== lastFrames && c.frames > 0) {
    lastFrames = c.frames;
    $("camera").src = `/camera.jpg?v=${c.frames}`;
    $("camera").hidden = false;
    $("camera-empty").hidden = true;
  } else if (!c.frames) {
    $("camera").hidden = true;
    $("camera-empty").hidden = false;
  }
}

async function pollCapture() {
  try {
    capture = await (await fetch("/capture/status")).json();
    renderCapture();
  } catch (err) { /* server restarting */ }
  setTimeout(pollCapture, capture && capture.running ? 500 : 1500);
}

$("source").addEventListener("change", showSourceFields);

wire("btn-capture", async () => {
  if (capture && capture.running) {
    capture = await post("/capture/stop");
    return renderCapture();
  }
  const source = $("source").value;
  const body = { source };
  if (source === "region") {
    const nums = $("region").value.split(/[ ,]+/).filter(Boolean).map(Number);
    if (nums.length !== 4 || nums.some(isNaN)) throw new Error("Region needs 4 numbers: x, y, width, height");
    body.region = nums;
  }
  if (source === "file") {
    body.path = $("file-path").value.trim();
    body.call_quality = $("call-quality").checked;
    body.loop = $("loop").checked;
    body.realtime = true;
  }
  lastFrames = -1;
  capture = await post("/capture/start", body);
  renderCapture();
});

$("record").addEventListener("change", async (ev) => {
  try { await post("/record", { on: ev.target.checked }); }
  catch (err) { alert(err.message); ev.target.checked = !ev.target.checked; }
});

showSourceFields();
pollCapture();

fetch("/settings").then((r) => r.json()).then((s) => {
  $("speak-on").value = s.speak_on || "phone";
  stableFrames = s.stable_frames || stableFrames;
  $("stable").textContent = stableFrames;
});
connect();
