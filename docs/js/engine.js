// KnightSide — © 2026 KnightSide. All rights reserved.
//
// Stockfish 18 (lite, single-threaded WebAssembly) in a Web Worker, spoken to over UCI.
// Searches are short and capped, so a casual game costs little battery or CPU.

const ENGINE_URL = new URL("../vendor/stockfish/stockfish-18-lite-single.js", import.meta.url);
const MOVE_TIME_MS = 500;      // plenty for casual games
const MAX_DEPTH = 16;

export class Engine {
  constructor() {
    this.worker = null;
    this.ready = null;
    this.waiting = null;       // resolve() for the search in progress
    this.queue = Promise.resolve();
  }

  start() {
    if (this.ready) return this.ready;
    this.worker = new Worker(ENGINE_URL);
    this.worker.onmessage = (ev) => this.onLine(String(ev.data));
    this.ready = new Promise((resolve) => {
      this.onReady = resolve;
      this.send("uci");
      this.send("setoption name Hash value 16");
      this.send("isready");
    });
    return this.ready;
  }

  send(cmd) { this.worker.postMessage(cmd); }

  onLine(line) {
    if (line === "readyok" && this.onReady) {
      this.onReady();
      this.onReady = null;
    } else if (line.startsWith("bestmove") && this.waiting) {
      const uci = line.split(" ")[1];
      const done = this.waiting;
      this.waiting = null;
      done(uci && uci !== "(none)" ? { from: uci.slice(0, 2), to: uci.slice(2, 4), promotion: uci[4] } : null);
    }
  }

  /** Best move for the side to move in `fen`, or null. Searches run one at a time. */
  bestMove(fen) {
    const run = async () => {
      await this.start();
      return new Promise((resolve) => {
        this.waiting = resolve;
        this.send(`position fen ${fen}`);
        this.send(`go movetime ${MOVE_TIME_MS} depth ${MAX_DEPTH}`);
      });
    };
    const result = this.queue.then(run, run);
    this.queue = result.catch(() => null);
    return result;
  }

  stop() {
    if (this.worker) this.worker.terminate();
    this.worker = this.ready = this.waiting = null;
  }
}

/** Speaks through the browser's voice (on the iPhone app: into the glasses). */
export class Speaker {
  constructor() {
    this.lastText = null;
    this.enabled = true;
    this.onSpeak = () => {};
  }

  say(text, force = false) {
    if (!force && text === this.lastText) return false;
    this.lastText = text;
    this.speak(text);
    return true;
  }

  repeat() {
    if (!this.lastText) return false;
    this.speak(this.lastText);
    return true;
  }

  speak(text) {
    this.onSpeak(text);
    if (!this.enabled || !("speechSynthesis" in window)) return;
    const u = new SpeechSynthesisUtterance(text);
    u.lang = "en-US";
    if (speechSynthesis.speaking || speechSynthesis.pending) speechSynthesis.cancel();
    this.current = u;  // Safari can drop an utterance that nothing references
    speechSynthesis.speak(u);
  }

  // iOS only lets a page speak after it has spoken from a tap. Call this from the first tap.
  unlock() {
    if (this.unlocked || !("speechSynthesis" in window)) return;
    this.unlocked = true;
    const u = new SpeechSynthesisUtterance(" ");
    u.volume = 0;
    speechSynthesis.speak(u);
  }
}
