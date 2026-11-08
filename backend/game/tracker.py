"""Turn per-square vision probabilities into confirmed chess moves.

Vision only reports empty / white / black per square. We enumerate every position
reachable from the last confirmed one (no change, all 1-ply moves, all 2-ply
sequences), score each against the observed probabilities, and accept the best
one only after it wins clearly on several consecutive frames.

Squares use python-chess indexing: 0 = a1, 7 = h1, 63 = h8.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import chess
import numpy as np

from backend import config

EMPTY, WHITE, BLACK = 0, 1, 2
PROB_FLOOR = 1e-3


def occupancy_of(board: chess.Board) -> np.ndarray:
    """64-length int8 array: 0 empty, 1 white piece, 2 black piece."""
    occ = np.zeros(64, dtype=np.int8)
    for sq in chess.SquareSet(board.occupied_co[chess.WHITE]):
        occ[sq] = WHITE
    for sq in chess.SquareSet(board.occupied_co[chess.BLACK]):
        occ[sq] = BLACK
    return occ


@dataclass(frozen=True)
class Candidate:
    moves: tuple[chess.Move, ...]
    occ: np.ndarray = field(compare=False)

    @property
    def key(self) -> bytes:
        return self.occ.tobytes()


def candidates(board: chess.Board, plies: int = 2) -> list[Candidate]:
    """All positions reachable in 0..`plies` plies, one per distinct occupancy.

    Promotions to different pieces look identical, so only queen promotion is kept.
    When two sequences of the same length give the same occupancy but different
    positions, that occupancy is dropped as ambiguous. Transpositions (same
    position by another move order) are not ambiguous.
    """
    found: dict[bytes, tuple[Candidate, str]] = {}
    ambiguous: set[bytes] = set()

    def add(moves: tuple[chess.Move, ...], b: chess.Board) -> None:
        cand = Candidate(moves, occupancy_of(b))
        placement = b.board_fen()
        prev = found.get(cand.key)
        if prev is None:
            found[cand.key] = (cand, placement)
        elif len(prev[0].moves) == len(moves) and prev[1] != placement \
                and _strip_promo(prev[0].moves) != _strip_promo(moves):
            ambiguous.add(cand.key)

    def walk(b: chess.Board, moves: tuple[chess.Move, ...]) -> None:
        add(moves, b)
        if len(moves) == plies:
            return
        for m in _queen_only(b.legal_moves):
            b.push(m)
            walk(b, moves + (m,))
            b.pop()

    walk(board.copy(stack=False), ())
    return [c for k, (c, _) in found.items() if k not in ambiguous]


def _queen_only(moves) -> list[chess.Move]:
    return [m for m in moves if m.promotion in (None, chess.QUEEN)]


def _strip_promo(moves: tuple[chess.Move, ...]) -> tuple[tuple[int, int], ...]:
    return tuple((m.from_square, m.to_square) for m in moves)


def log_probs(probs: np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(probs, dtype=np.float64), PROB_FLOOR, 1.0)
    return np.log(p / p.sum(axis=1, keepdims=True))


@dataclass
class Ranked:
    candidate: Candidate
    score: float            # log-likelihood incl. 2-ply penalty
    margin: float           # score gap to the runner-up (inf if only one candidate)
    mismatches: int         # squares where the observation clearly disagrees with this candidate


def rank(cands: list[Candidate], probs: np.ndarray) -> Ranked:
    """Best candidate for a (64, 3) probability array."""
    lp = log_probs(probs)
    occs = np.stack([c.occ for c in cands])                        # (N, 64)
    scores = lp[np.arange(64), occs].sum(axis=1)
    # Each ply beyond the first costs TWO_PLY_PENALTY: prefer the shortest explanation.
    scores -= np.array([config.TWO_PLY_PENALTY * max(0, len(c.moves) - 1) for c in cands])
    order = np.argsort(scores)[::-1]
    best = order[0]
    margin = float(scores[best] - scores[order[1]]) if len(order) > 1 else float("inf")
    expected = np.exp(lp[np.arange(64), occs[best]])
    mismatches = int((expected < config.MISMATCH_PROB).sum())
    return Ranked(cands[best], float(scores[best]), margin, mismatches)


@dataclass
class TrackResult:
    status: str             # "no_change" | "pending" | "uncertain" | "bad" | "check_board" | "move"
    moves: tuple[chess.Move, ...] = ()
    score: float | None = None
    margin: float | None = None


class Tracker:
    """Holds the confirmed game and decides when the board has really changed."""

    def __init__(self, board: chess.Board | None = None):
        self.board = board.copy() if board else chess.Board()
        self._cands: list[Candidate] | None = None
        self._deep: list[Candidate] | None = None      # 3-ply candidates, only while stuck
        self._pending: bytes | None = None
        self._pending_count = 0
        self._bad_count = 0
        self._warned = False

    def observe(self, probs: np.ndarray) -> TrackResult:
        """Feed one good frame's (64, 3) probabilities [empty, white, black]."""
        if self._bad_count >= config.BAD_LIMIT and self._deep is None:
            # Stuck: maybe three plies happened while nothing could be confirmed. Look one ply deeper.
            self._deep = candidates(self.board, plies=3)
        if self._deep is not None:
            cands = self._deep
        else:
            if self._cands is None:
                self._cands = candidates(self.board)
            cands = self._cands
        r = rank(cands, probs)

        if r.mismatches > config.MAX_MISMATCH:
            # Not a good frame (a hand over the board, one misread square): it neither
            # counts towards nor breaks the run of good frames behind a pending move.
            self._bad_count += 1
            if self._bad_count >= config.BAD_LIMIT and not self._warned:
                self._warned = True
                return TrackResult("check_board", score=r.score, margin=r.margin)
            return TrackResult("bad", score=r.score, margin=r.margin)

        self._bad_count = 0
        self._warned = False

        if r.margin < config.MIN_MARGIN:
            self._reset_pending()
            return TrackResult("uncertain", score=r.score, margin=r.margin)

        key = r.candidate.key
        if key == self._pending:
            self._pending_count += 1
        else:
            self._pending, self._pending_count = key, 1

        if self._pending_count < config.STABLE_FRAMES:
            return TrackResult("pending", r.candidate.moves, r.score, r.margin)

        self._reset_pending()
        self._deep = None
        if not r.candidate.moves:
            return TrackResult("no_change", score=r.score, margin=r.margin)
        for m in r.candidate.moves:
            self.board.push(m)
        self._cands = None
        return TrackResult("move", r.candidate.moves, r.score, r.margin)

    def undo(self) -> chess.Move | None:
        if not self.board.move_stack:
            return None
        self._reset_pending()
        self._cands = self._deep = None
        return self.board.pop()

    def _reset_pending(self) -> None:
        self._pending, self._pending_count = None, 0
