// KnightSide — © 2026 KnightSide. All rights reserved.
//
// Game session: turns per-frame vision output into spoken best moves.
// Vision calls observe() with 64 rows of [empty, white, black]; everything else here
// is chess state and deciding what to say.

import { Chess } from "../vendor/chess/chess.js";
import { START, TUNING, Tracker, gameOver, moveToSpeech, occupancyOf, rank } from "./tracker.js";

export const STATUS = {
  IDLE: "idle",                 // no game; press New game
  CALIBRATING: "calibrating",   // waiting to see the starting position
  WAITING: "waiting",           // opponent to move
  YOUR_MOVE: "your_move",
  CHECK_BOARD: "check_board",   // nothing legal fits what we see
  GAME_OVER: "game_over",
};

const COLOR_NAME = { w: "White", b: "Black" };

export class GameSession {
  /**
   * engine: {bestMove(fen) -> Promise<{from,to,promotion}|null>}
   * speaker: {say(text, force?), repeat(), lastText}
   */
  constructor(engine, speaker) {
    this.engine = engine;
    this.speaker = speaker;
    this.listeners = [];        // (kind, data) game events: the board reader learns from them
    this.onChange = () => {};
    this.reset();
  }

  reset() {
    this.status = STATUS.IDLE;
    this.tracker = null;
    this.userColor = null;      // "w" | "b"
    this.colorOverride = null;
    this.bestMove = null;
    this.result = null;
    this.boardVisible = false;
    this.lastProbs = null;
    this.lastConfidence = null;
    this.calibCount = 0;
  }

  // ---------- controls ----------

  newGame(userColor = null) {
    this.reset();
    this.status = STATUS.CALIBRATING;
    this.colorOverride = userColor;
    this.emit("new_game");
    this.changed();
  }

  lock(userColor) {
    this.userColor = userColor;
    this.tracker = new Tracker();
    this.emit("lock", { userColor, fen: this.tracker.chess.fen() });
    this.say(`Board locked, you are ${COLOR_NAME[userColor]}`);
    this.afterPositionChange();
  }

  undo() {
    if (!this.tracker) return null;
    const move = this.tracker.undo();
    if (!move) return null;
    this.bestMove = null;
    this.emit("undo", { fen: this.tracker.chess.fen() });
    this.afterPositionChange();
    return move;
  }

  /** Relearn what the squares look like; the game itself is kept. */
  recalibrate() {
    this.emit("recalibrate", { fen: this.tracker ? this.tracker.chess.fen() : null });
    this.changed();
  }

  endGame() {
    this.emit("end");
    this.reset();
    this.changed();
  }

  repeat() { return this.speaker.repeat(); }

  // ---------- frames ----------

  /** One frame with a visible board. `seenColor` is the side vision saw nearest the camera. */
  observe(probs, confidence = 1, seenColor = null) {
    this.boardVisible = true;
    this.lastProbs = probs;
    this.lastConfidence = confidence;
    if (confidence >= TUNING.minConfidence && this.status !== STATUS.IDLE && this.status !== STATUS.GAME_OVER) {
      if (this.status === STATUS.CALIBRATING) this.calibrateStep(probs, seenColor);
      else this.trackStep(probs);
    }
    this.changed();
    return this.status;
  }

  /** Frame without a board (looking away): stay silent, just show it. */
  observeNoBoard() {
    if (this.boardVisible) {
      this.boardVisible = false;
      this.changed();
    }
  }

  // ---------- state for the page ----------

  get chess() { return this.tracker ? this.tracker.chess : new Chess(); }

  state() {
    const chess = this.chess;
    const verbose = chess.history({ verbose: true });
    return {
      status: this.status,
      boardVisible: this.boardVisible,
      userColor: this.userColor ? COLOR_NAME[this.userColor] : null,
      fen: chess.fen(),
      moves: verbose.map((m) => m.san),
      lastMove: verbose.length ? verbose[verbose.length - 1] : null,
      bestMove: this.bestMove ? this.bestMove.san : null,
      bestMoveFromTo: this.bestMove ? [this.bestMove.from, this.bestMove.to] : null,
      spoken: this.speaker.lastText,
      result: this.result,
      occupancy: this.lastProbs ? this.lastProbs.map((r) => r.indexOf(Math.max(...r))) : null,
      expectedOccupancy: occupancyOf(chess),
      confidence: this.lastConfidence,
    };
  }

  // ---------- internals ----------

  calibrateStep(probs, seenColor) {
    // Orientation and colour detection live in the board reader. Here we only
    // need the starting position to be seen clearly for stableFrames frames.
    this.calibCount = rank(START, probs).mismatches === 0 ? this.calibCount + 1 : 0;
    if (this.calibCount >= TUNING.stableFrames) this.lock(this.colorOverride ?? seenColor ?? "w");
  }

  trackStep(probs) {
    const r = this.tracker.observe(probs);
    if (r.status === "check_board") {
      this.status = STATUS.CHECK_BOARD;
      this.say("Check the board");
    } else if (r.status === "move") {
      this.bestMove = null;
      this.emit("move", { fen: this.tracker.chess.fen() });
      this.afterPositionChange();
    } else if (r.status === "no_change" && this.status === STATUS.CHECK_BOARD) {
      this.afterPositionChange();
    }
  }

  afterPositionChange() {
    const chess = this.tracker.chess;
    const over = gameOver(chess);
    if (over) {
      this.status = STATUS.GAME_OVER;
      this.result = over;
      this.say(over);
    } else if (chess.turn() === this.userColor) {
      this.status = STATUS.YOUR_MOVE;
      this.suggest(chess.fen());
    } else {
      this.status = STATUS.WAITING;
      this.bestMove = null;
    }
    this.changed();
  }

  async suggest(fen) {
    this.bestMove = null;
    const move = await this.engine.bestMove(fen);
    // The board may have moved on while the engine was thinking.
    if (!move || !this.tracker || this.tracker.chess.fen() !== fen || this.status !== STATUS.YOUR_MOVE) return;
    const san = new Chess(fen).move(move).san;
    this.bestMove = { ...move, san };
    this.say(moveToSpeech(fen, move));
    this.changed();
  }

  say(text) { this.speaker.say(text); }

  emit(kind, data = {}) {
    for (const listener of this.listeners) {
      try { listener(kind, data); } catch (err) { console.error("game event listener failed", err); }
    }
  }

  changed() { this.onChange(this.state()); }
}
