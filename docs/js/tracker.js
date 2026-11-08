// KnightSight — © 2026 KnightSight. All rights reserved.
//
// Turn per-square vision probabilities into confirmed chess moves.
//
// Vision only reports empty / white / black per square. We enumerate every position
// reachable from the last confirmed one (no change, all 1-ply moves, all 2-ply
// sequences), score each against the observed probabilities, and accept the best
// one only after it wins clearly on several consecutive frames.
//
// Squares are 0 = a1 ... 7 = h1 ... 63 = h8. Probabilities are 64 rows of [empty, white, black].

import { Chess } from "../vendor/chess/chess.js";

export const TUNING = {
  minBoardQuality: 0.5,
  minConfidence: 0.8,
  minVisibleSquares: 48,
  stableFrames: 3,
  minMargin: 2.0,
  mismatchProb: 0.3,
  maxMismatch: 1,        // one hidden square is normal
  badLimit: 12,          // ~4 s of frames nothing fits before "Check the board"
  twoPlyPenalty: 2.0,
};

export const EMPTY = 0, WHITE = 1, BLACK = 2;
const FILES = "abcdefgh";

export const squareName = (sq) => FILES[sq % 8] + (Math.floor(sq / 8) + 1);
export const squareIndex = (name) => FILES.indexOf(name[0]) + 8 * (Number(name[1]) - 1);

/** 64-length array: 0 empty, 1 white piece, 2 black piece. */
export function occupancyOf(chess) {
  const occ = new Array(64).fill(EMPTY);
  const rows = chess.board();                 // rank 8 first, file a first
  for (let r = 0; r < 8; r++) {
    for (let f = 0; f < 8; f++) {
      const p = rows[r][f];
      if (p) occ[(7 - r) * 8 + f] = p.color === "w" ? WHITE : BLACK;
    }
  }
  return occ;
}

const keyOf = (occ) => occ.join("");
const placement = (chess) => chess.fen().split(" ")[0];

/**
 * All positions reachable in 0..plies plies, one per distinct occupancy: [{moves, occ, key}].
 * Promotions to different pieces look identical, so only queen promotion is kept.
 * When two sequences of the same length give the same occupancy but different positions,
 * that occupancy is dropped as ambiguous. Transpositions are not ambiguous.
 */
export function candidates(fen, plies = 2) {
  const chess = new Chess(fen);
  const found = new Map();      // key -> {cand, placement}
  const ambiguous = new Set();
  const fromTo = (moves) => moves.map((m) => m.from + m.to).join(" ");

  const add = (moves) => {
    const occ = occupancyOf(chess);
    const key = keyOf(occ);
    const place = placement(chess);
    const prev = found.get(key);
    if (!prev) {
      found.set(key, { cand: { moves, occ, key }, place });
    } else if (prev.cand.moves.length === moves.length && prev.place !== place
               && fromTo(prev.cand.moves) !== fromTo(moves)) {
      ambiguous.add(key);
    }
  };

  const walk = (moves) => {
    add(moves);
    if (moves.length === plies) return;
    for (const m of chess.moves({ verbose: true })) {
      if (m.promotion && m.promotion !== "q") continue;
      chess.move({ from: m.from, to: m.to, promotion: m.promotion });
      walk([...moves, { from: m.from, to: m.to, promotion: m.promotion }]);
      chess.undo();
    }
  };

  walk([]);
  return [...found.values()].filter((v) => !ambiguous.has(v.cand.key)).map((v) => v.cand);
}

export function logProbs(probs) {
  return probs.map((row) => {
    const p = row.map((x) => Math.min(Math.max(x, 1e-3), 1));
    const s = p[0] + p[1] + p[2];
    return p.map((x) => Math.log(x / s));
  });
}

/** Best candidate for the observed probabilities: {candidate, score, margin, mismatches}. */
export function rank(cands, probs) {
  const lp = logProbs(probs);
  let best = -1, bestScore = -Infinity, second = -Infinity;
  cands.forEach((c, i) => {
    let s = 0;
    for (let sq = 0; sq < 64; sq++) s += lp[sq][c.occ[sq]];
    // Each ply beyond the first costs twoPlyPenalty: prefer the shortest explanation.
    s -= TUNING.twoPlyPenalty * Math.max(0, c.moves.length - 1);
    if (s > bestScore) { second = bestScore; bestScore = s; best = i; }
    else if (s > second) second = s;
  });
  const cand = cands[best];
  let mismatches = 0;
  for (let sq = 0; sq < 64; sq++) if (Math.exp(lp[sq][cand.occ[sq]]) < TUNING.mismatchProb) mismatches++;
  return { candidate: cand, score: bestScore, margin: cands.length > 1 ? bestScore - second : Infinity, mismatches };
}

/** Holds the confirmed game and decides when the board has really changed. */
export class Tracker {
  constructor() {
    this.chess = new Chess();
    this.cands = null;
    this.deep = null;          // 3-ply candidates, only while stuck
    this.pending = null;
    this.pendingCount = 0;
    this.badCount = 0;
    this.warned = false;
  }

  /** One good frame. Returns {status, moves}: no_change | pending | uncertain | bad | check_board | move. */
  observe(probs) {
    if (this.badCount >= TUNING.badLimit && !this.deep) {
      // Stuck: maybe three plies happened while nothing could be confirmed. Look one ply deeper.
      this.deep = candidates(this.chess.fen(), 3);
    }
    let cands = this.deep;
    if (!cands) cands = this.cands ??= candidates(this.chess.fen());
    const r = rank(cands, probs);

    if (r.mismatches > TUNING.maxMismatch) {
      // Not a good frame (a hand over the board, one misread square): it neither
      // counts towards nor breaks the run of good frames behind a pending move.
      this.badCount++;
      if (this.badCount >= TUNING.badLimit && !this.warned) {
        this.warned = true;
        return { status: "check_board", moves: [] };
      }
      return { status: "bad", moves: [] };
    }
    this.badCount = 0;
    this.warned = false;

    if (r.margin < TUNING.minMargin) {
      this.resetPending();
      return { status: "uncertain", moves: [] };
    }
    const key = r.candidate.key;
    if (key === this.pending) this.pendingCount++;
    else { this.pending = key; this.pendingCount = 1; }
    if (this.pendingCount < TUNING.stableFrames) return { status: "pending", moves: r.candidate.moves };

    this.resetPending();
    this.deep = null;
    if (!r.candidate.moves.length) return { status: "no_change", moves: [] };
    for (const m of r.candidate.moves) this.chess.move(m);
    this.cands = null;
    return { status: "move", moves: r.candidate.moves };
  }

  undo() {
    if (!this.chess.history().length) return null;
    this.resetPending();
    this.cands = this.deep = null;
    return this.chess.undo();
  }

  resetPending() {
    this.pending = null;
    this.pendingCount = 0;
  }
}

/** The start position as a one-candidate list, for calibration. */
export const START = candidates(new Chess().fen(), 0);

const NAMES = { p: "Pawn", n: "Knight", b: "Bishop", r: "Rook", q: "Queen", k: "King" };

/** A move as a short plain-English phrase for speech. `move` is {from, to, promotion} legal in `fen`. */
export function moveToSpeech(fen, move) {
  const chess = new Chess(fen);
  const legal = chess.moves({ verbose: true });
  const m = legal.find((x) => x.from === move.from && x.to === move.to && (x.promotion || null) === (move.promotion || null));
  let text;
  if (m.flags.includes("k")) text = "Castle kingside";
  else if (m.flags.includes("q")) text = "Castle queenside";
  else {
    const verb = m.flags.includes("c") || m.flags.includes("e") ? "takes" : "to";
    const others = m.piece === "p" ? [] : legal.filter((x) => x.to === m.to && x.from !== m.from && x.piece === m.piece);
    text = others.length ? `${NAMES[m.piece]} from ${m.from} ${verb} ${m.to}` : `${NAMES[m.piece]} ${verb} ${m.to}`;
    if (m.promotion) text += `, promote to ${NAMES[m.promotion].toLowerCase()}`;
  }
  chess.move(m);
  if (chess.isCheckmate()) text += ", checkmate";
  else if (chess.inCheck()) text += ", check";
  return text;
}

/** python-chess `is_game_over()`: mate, stalemate, insufficient material, 75 moves, fivefold repetition. */
export function gameOver(chess) {
  if (chess.isCheckmate()) return "Checkmate";
  if (chess.isStalemate()) return "Stalemate";
  if (chess.isInsufficientMaterial()) return "Draw";
  if (Number(chess.fen().split(" ")[4]) >= 150) return "Draw";
  // Fivefold: count the current position (placement, side, castling, en passant) in the game so far.
  const key = (fen) => fen.split(" ").slice(0, 4).join(" ");
  const replay = new Chess();
  const seen = [key(replay.fen())];
  for (const m of chess.history({ verbose: true })) { replay.move(m); seen.push(key(replay.fen())); }
  const now = key(chess.fen());
  return seen.filter((k) => k === now).length >= 5 ? "Draw" : null;
}
