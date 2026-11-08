// KnightSide — © 2026 KnightSide. All rights reserved.
//
// The play page: camera -> board reader -> game session -> board, best move and voice.
// In the KnightSide iPhone app (?source=glasses) the frames come from the glasses instead
// of a webcam, and phrases are spoken by the app so they play in the glasses.

import { loadOpenCV, BoardReader } from "./vision.js";
import { GameSession, STATUS } from "./game.js";
import { Engine, Speaker } from "./engine.js";
import { squareIndex } from "./tracker.js";

const $ = (id) => document.getElementById(id);
const FRAME_MS = 333;              // three frames a second is enough to follow a game
const MAX_SIDE = 1280;
const params = new URLSearchParams(location.search);
const native = window.webkit?.messageHandlers?.knightside;
const glassesMode = params.get("source") === "glasses" && !!native;

const engine = new Engine();
const speaker = new Speaker();
const session = new GameSession(engine, speaker);
let reader = null;
let cv = null;
let state = session.state();
let running = false;
let busy = false;
let stream = null;
let wakeLock = null;
const canvas = document.createElement("canvas");
const ctx = canvas.getContext("2d", { willReadFrequently: true });

if (glassesMode) {
  speaker.speak = (text) => { speaker.onSpeak(text); native.postMessage({ type: "speak", text }); };
}
speaker.onSpeak = (text) => { $("spoken").textContent = `Said: “${text}”`; };

// ---------- loading ----------

const visionReady = loadOpenCV(new URL("../vendor/opencv/opencv.js", import.meta.url).href).then((lib) => {
  cv = lib;
  reader = new BoardReader();
  session.listeners.push((kind, data) => reader.onEvent(kind, data));
  $("loading").textContent = "";
  render();
}).catch((err) => {
  $("loading").textContent = `Couldn't load the vision library: ${err.message}. Reload the page to try again.`;
});
$("loading").textContent = "Loading the vision library (about 13 MB, first visit only)…";
engine.start();

// ---------- camera ----------

async function startCamera(deviceId) {
  if (!navigator.mediaDevices?.getUserMedia) {
    $("camera-msg").textContent = "This browser can't use a camera. Try a recent Chrome, Edge, Safari or Firefox.";
    return;
  }
  stopCamera();
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: deviceId ? { deviceId: { exact: deviceId }, width: { ideal: 1280 }, height: { ideal: 720 } }
        : { width: { ideal: 1280 }, height: { ideal: 720 }, facingMode: "environment" },
      audio: false,
    });
  } catch (err) {
    $("camera-msg").textContent = err.name === "NotAllowedError"
      ? "Camera access was blocked. Allow it in the browser's address bar, then try again."
      : `Couldn't start the camera (${err.name}).`;
    return;
  }
  const video = $("video");
  video.srcObject = stream;
  await video.play();
  $("camera-empty").hidden = true;
  await listCameras(stream.getVideoTracks()[0]?.getSettings().deviceId);
  run(true);
}

function stopCamera() {
  if (stream) stream.getTracks().forEach((t) => t.stop());
  stream = null;
}

async function listCameras(current) {
  const cams = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "videoinput");
  const sel = $("camera-select");
  sel.hidden = cams.length < 2;
  sel.innerHTML = cams.map((c, i) => `<option value="${c.deviceId}">${c.label || `Camera ${i + 1}`}</option>`).join("");
  if (current) sel.value = current;
}

async function run(on) {
  running = on;
  try {
    if (on && "wakeLock" in navigator && !wakeLock) {
      wakeLock = await navigator.wakeLock.request("screen");
      wakeLock.addEventListener("release", () => { wakeLock = null; });
    }
    if (!on && wakeLock) { await wakeLock.release(); wakeLock = null; }
  } catch { /* the screen may still sleep; not fatal */ }
  render();
}

// ---------- frames ----------

/** Read one frame from any drawable source (video or image element). */
function processDrawable(source, width, height) {
  if (!reader || busy || !width || !height) return;
  busy = true;
  try {
    const scale = Math.min(1, MAX_SIDE / Math.max(width, height));
    canvas.width = Math.round(width * scale);
    canvas.height = Math.round(height * scale);
    ctx.drawImage(source, 0, 0, canvas.width, canvas.height);
    const rgba = cv.matFromImageData(ctx.getImageData(0, 0, canvas.width, canvas.height));
    const bgr = new cv.Mat();
    cv.cvtColor(rgba, bgr, cv.COLOR_RGBA2BGR);
    rgba.delete();
    let result;
    try { result = reader.read(bgr); } finally { bgr.delete(); }
    if (result) session.observe(result.probs, result.confidence, result.userColor);
    else session.observeNoBoard();
    drawGrid(canvas.width, canvas.height);
    render();
  } catch (err) {
    console.error(err);
  } finally {
    busy = false;
  }
}

setInterval(() => {
  if (!running || document.hidden || glassesMode) return;
  const v = $("video");
  if (v.readyState >= 2) processDrawable(v, v.videoWidth, v.videoHeight);
}, FRAME_MS);

/** Glasses frames from the iPhone app, as JPEG data URLs. */
window.KnightSide = {
  async pushFrame(dataUrl) {
    if (busy || !reader) return false;
    const img = $("still");
    img.src = dataUrl;
    await img.decode();
    img.hidden = false;
    $("camera-empty").hidden = true;
    if (!running) await run(true);
    processDrawable(img, img.naturalWidth, img.naturalHeight);
    return true;
  },
  command(name) { commands[name]?.(); },
  state: () => state,
};

function drawGrid(w, h) {
  const o = $("overlay");
  o.width = w;
  o.height = h;
  const g = o.getContext("2d");
  g.clearRect(0, 0, w, h);
  g.lineCap = "round";
  for (const line of reader.grid(w)) {
    g.strokeStyle = line.near ? "#ff8a00" : "rgba(80, 255, 140, 0.9)";
    g.lineWidth = Math.max(w, h) / (line.near ? 160 : 360);
    g.beginPath();
    line.pts.forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y)));
    g.stroke();
  }
}

// ---------- rendering ----------

const GLYPHS = { k: "♚", q: "♛", r: "♜", b: "♝", n: "♞", p: "♟" };

function headline(s) {
  if (!running) return glassesMode ? "Waiting for the glasses" : "Turn on the camera to start";
  switch (s.status) {
    case STATUS.IDLE: return "Press New game";
    case STATUS.CALIBRATING: return s.boardVisible ? "Hold still on the starting position" : "Show the starting position";
    case STATUS.YOUR_MOVE: return "Your move";
    case STATUS.WAITING: return "Opponent's move";
    case STATUS.CHECK_BOARD: return "Check the board";
    case STATUS.GAME_OVER: return `Game over: ${s.result}`;
    default: return "";
  }
}

const STATUS_TEXT = {
  idle: "Ready", calibrating: "Calibrating", your_move: "Your move", waiting: "Waiting",
  check_board: "Check the board", game_over: "Game over",
};

function render() {
  state = session.state();
  const pill = $("status");
  pill.dataset.status = state.status;
  pill.textContent = STATUS_TEXT[state.status];
  $("headline").textContent = headline(state);
  const showHint = reader?.hint && (state.status === STATUS.CALIBRATING || state.status === STATUS.CHECK_BOARD);
  $("hint").hidden = !showHint;
  $("hint").textContent = showHint ? reader.hint : "";
  $("color").textContent = state.userColor ? `You are ${state.userColor}` : "";
  $("best").textContent = state.bestMove || "—";
  $("vision-note").textContent = running && reader ? `Vision: ${reader.note}` : "";
  const ready = running && !!reader;
  $("btn-new").disabled = !ready;
  const inGame = ready && state.status !== STATUS.IDLE;
  ["btn-repeat", "btn-undo", "btn-recal", "btn-end"].forEach((id) => { $(id).disabled = !inGame; });
  renderBoard();
  renderMoves();
  if (glassesMode) native.postMessage({ type: "state", status: state.status, headline: headline(state), best: state.bestMove });
}

function renderBoard() {
  const flipped = state.userColor === "Black";
  const rows = state.fen.split(" ")[0].split("/");
  const pieces = new Array(64).fill(null);
  rows.forEach((row, i) => {
    let f = 0;
    for (const ch of row) {
      if (/\d/.test(ch)) { f += Number(ch); continue; }
      pieces[(7 - i) * 8 + f] = ch;
      f++;
    }
  });
  const at = (sq) => {
    const file = sq % 8, rank = Math.floor(sq / 8);
    return flipped ? [(7 - file) * 100, rank * 100] : [file * 100, (7 - rank) * 100];
  };
  let svg = `<svg viewBox="0 0 800 800" role="img" aria-label="Chess board">`;
  for (let sq = 0; sq < 64; sq++) {
    const [x, y] = at(sq);
    const light = (Math.floor(sq / 8) + (sq % 8)) % 2 === 1;
    svg += `<rect x="${x}" y="${y}" width="100" height="100" fill="var(${light ? "--light-sq" : "--dark-sq"})"/>`;
    const p = pieces[sq];
    if (p) {
      const white = p === p.toUpperCase();
      svg += `<text x="${x + 50}" y="${y + 74}" font-size="74" text-anchor="middle" fill="${white ? "#fff" : "#0b1f4d"}"
        stroke="${white ? "#0b1f4d" : "#000"}" stroke-width="2.5">${GLYPHS[p.toLowerCase()]}</text>`;
    }
  }
  if (state.bestMoveFromTo) {
    const c = (name) => at(squareIndex(name)).map((v) => v + 50);
    const [a, b] = state.bestMoveFromTo.map(c);
    svg += `<defs><marker id="head" markerWidth="4" markerHeight="4" refX="2.2" refY="2" orient="auto">
      <path d="M0,0 L4,2 L0,4 z" fill="#1546ff"/></marker></defs>
      <line x1="${a[0]}" y1="${a[1]}" x2="${b[0]}" y2="${b[1]}" stroke="#1546ff" stroke-opacity="0.85"
        stroke-width="16" stroke-linecap="round" marker-end="url(#head)"/>`;
  }
  $("board").innerHTML = svg + "</svg>";
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
  ol.scrollTop = ol.scrollHeight;
}

// ---------- controls ----------

const commands = {
  newGame: () => session.newGame($("new-color").value || null),
  repeat: () => session.repeat(),
  undo: () => session.undo(),
  recalibrate: () => session.recalibrate(),
  end: () => session.endGame(),
};
// The engine answers later than the frame that asked, so re-render on every session change.
let renderQueued = false;
session.onChange = () => {
  if (renderQueued) return;
  renderQueued = true;
  requestAnimationFrame(() => { renderQueued = false; render(); });
};

const wire = (id, fn) => $(id).addEventListener("click", () => { fn(); render(); });
wire("btn-camera", () => startCamera());
wire("btn-new", commands.newGame);
wire("btn-repeat", commands.repeat);
wire("btn-undo", commands.undo);
wire("btn-recal", commands.recalibrate);
wire("btn-end", commands.end);
$("camera-select").addEventListener("change", (ev) => startCamera(ev.target.value));
$("voice").addEventListener("change", (ev) => { speaker.enabled = ev.target.checked; });
document.addEventListener("visibilitychange", async () => {
  // The wake lock is dropped when the tab is hidden; take it again on return.
  if (!document.hidden && running && !wakeLock && "wakeLock" in navigator) {
    try { wakeLock = await navigator.wakeLock.request("screen"); } catch { /* not fatal */ }
  }
});

if (glassesMode) {
  $("btn-camera").hidden = true;
  $("camera-msg").textContent = "Waiting for frames from the glasses…";
  native.postMessage({ type: "ready" });
}
visionReady.then(render);
render();
