"""Game session: turns per-frame vision output into spoken best moves.

Vision (Phase 4) and the debug route both call `observe()` with (64, 3)
probabilities [empty, white, black]. Everything else here is chess state and
deciding what to say. All public methods are thread-safe.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import chess
import chess.pgn
import numpy as np

from backend import config
from backend.game.speech import move_to_speech
from backend.game.tracker import Candidate, Tracker, occupancy_of, rank

START_POSITION = [Candidate((), occupancy_of(chess.Board()))]

log = logging.getLogger(__name__)

# Statuses shown on the dashboard
IDLE = "idle"                 # no game; press New game
CALIBRATING = "calibrating"   # waiting to see the starting position
WAITING = "waiting"           # opponent to move
YOUR_MOVE = "your_move"
CHECK_BOARD = "check_board"   # nothing legal fits what we see
GAME_OVER = "game_over"

COLOR_NAMES = {chess.WHITE: "White", chess.BLACK: "Black"}


class GameSession:
    def __init__(self, engine, speaker, games_dir: Path | None = None):
        self.engine = engine
        self.speaker = speaker
        self.games_dir = games_dir or config.GAMES_DIR
        self._lock = threading.RLock()
        self.version = 0
        self.listeners: list[Callable[[str, dict], None]] = []   # (kind, data) game events, e.g. for recording
        self._reset()

    # ---------- controls ----------

    def new_game(self, user_color: chess.Color | None = None) -> None:
        """Start calibrating. `user_color` overrides auto-detection (debug path defaults to White)."""
        with self._lock:
            self._reset()
            self.status = CALIBRATING
            self.color_override = user_color
            self._changed()
            self._emit("new_game")

    def lock(self, user_color: chess.Color) -> None:
        """Called once calibration sees a stable starting position."""
        with self._lock:
            self.user_color = user_color
            self.tracker = Tracker()
            self.started_at = datetime.now()
            self._emit("lock", user_color=COLOR_NAMES[user_color], fen=self.tracker.board.fen())
            self._say(f"Board locked, you are {COLOR_NAMES[user_color]}")
            self._after_position_change()

    def undo(self) -> str | None:
        with self._lock:
            if self.tracker is None:
                return None
            move = self.tracker.undo()
            if move is None:
                return None
            self.best_move = None
            self._emit("undo", uci=move.uci(), fen=self.tracker.board.fen())
            self._after_position_change()
            return move.uci()

    def recalibrate(self) -> None:
        """Ask vision to relearn what the squares look like; the game itself is kept."""
        with self._lock:
            self._emit("recalibrate", fen=self.tracker.board.fen() if self.tracker else None)
            self._changed()

    def repeat(self) -> bool:
        with self._lock:
            return self.speaker.repeat()

    def end_game(self) -> str | None:
        with self._lock:
            pgn = self._save_pgn() if self.tracker and self.tracker.board.move_stack else None
            self._emit("end")
            self._reset()
            self._changed()
            return pgn

    # ---------- frames ----------

    def observe(self, probs: np.ndarray, confidence: float = 1.0, user_color: chess.Color | None = None) -> str:
        """Feed one frame with a visible board. Returns the status afterwards.

        `user_color` is the side vision saw nearest the camera; used at lock unless overridden.
        """
        probs = np.asarray(probs, dtype=np.float64)
        with self._lock:
            self.board_visible = True
            self.last_probs = probs
            self.last_confidence = confidence
            if confidence < config.MIN_CONF or self.status in (IDLE, GAME_OVER):
                self._changed()
                return self.status
            if self.status == CALIBRATING:
                self._calibrate_step(probs, user_color)
            else:
                self._track_step(probs)
            self._changed()
            return self.status

    def observe_no_board(self) -> None:
        """Frame without a board (looking away): stay silent, just show it."""
        with self._lock:
            if self.board_visible:
                self.board_visible = False
                self._changed()

    # ---------- state for the dashboard ----------

    def confirmed_fen(self) -> str | None:
        """FEN of the last confirmed position, or None before the board is locked."""
        with self._lock:
            return self.tracker.board.fen() if self.tracker else None

    def state(self) -> dict:
        with self._lock:
            board = self.tracker.board if self.tracker else chess.Board()
            occ = None
            if self.last_probs is not None:
                occ = self.last_probs.argmax(axis=1).tolist()
            return {
                "version": self.version,
                "status": self.status,
                "board_visible": self.board_visible,
                "user_color": COLOR_NAMES.get(self.user_color),
                "fen": board.fen(),
                "moves": _san_list(board),
                "last_move": _san_list(board)[-1] if board.move_stack else None,
                "best_move": board.san(self.best_move) if self.best_move else None,
                "best_move_uci": self.best_move.uci() if self.best_move else None,
                "spoken": self.speaker.last_text,
                "result": self.result,
                "occupancy": occ,
                "expected_occupancy": occupancy_of(board).tolist(),
                "confidence": self.last_confidence,
                "last_pgn_path": str(self.last_pgn_path) if self.last_pgn_path else None,
            }

    # ---------- internals ----------

    def _reset(self) -> None:
        self.status = IDLE
        self.tracker: Tracker | None = None
        self.user_color: chess.Color | None = None
        self.color_override: chess.Color | None = None
        self.best_move: chess.Move | None = None
        self.result: str | None = None
        self.board_visible = False
        self.last_probs: np.ndarray | None = None
        self.last_confidence: float | None = None
        self.started_at: datetime | None = None
        self.last_pgn_path: Path | None = getattr(self, "last_pgn_path", None)
        self._calib_count = 0

    def _calibrate_step(self, probs: np.ndarray, seen_color: chess.Color | None) -> None:
        # Orientation and colour detection live in vision/reader.py.
        # Here we only need the starting position to be seen clearly for STABLE_FRAMES.
        if rank(START_POSITION, probs).mismatches == 0:
            self._calib_count += 1
        else:
            self._calib_count = 0
        if self._calib_count >= config.STABLE_FRAMES:
            color = next(c for c in (self.color_override, seen_color, chess.WHITE) if c is not None)
            self.lock(color)

    def _track_step(self, probs: np.ndarray) -> None:
        result = self.tracker.observe(probs)
        if result.status == "check_board":
            self.status = CHECK_BOARD
            self._say("Check the board")
        elif result.status == "move":
            self.best_move = None
            self._emit("move", uci=[m.uci() for m in result.moves], fen=self.tracker.board.fen())
            self._after_position_change()
        elif result.status == "no_change" and self.status == CHECK_BOARD:
            self._after_position_change()

    def _after_position_change(self) -> None:
        board = self.tracker.board
        if board.is_game_over():
            self.status = GAME_OVER
            self.result = _result_phrase(board)
            self._say(self.result)
            self._save_pgn()
        elif board.turn == self.user_color:
            self.status = YOUR_MOVE
            self.best_move = self.engine.best_move(board)
            if self.best_move is not None:
                self._say(move_to_speech(board, self.best_move))
        else:
            self.status = WAITING
            self.best_move = None
        self._changed()

    def _say(self, text: str) -> None:
        log.info("say: %s", text)
        self.speaker.say(text)

    def _changed(self) -> None:
        self.version += 1

    def _emit(self, kind: str, **data) -> None:
        for listener in self.listeners:
            try:
                listener(kind, data)
            except Exception:
                log.exception("game event listener failed")

    def _save_pgn(self) -> str:
        board = self.tracker.board
        game = chess.pgn.Game.from_board(board)
        when = self.started_at or datetime.now()
        game.headers["Event"] = "Meta Chess casual game"
        game.headers["Date"] = when.strftime("%Y.%m.%d")
        you, them = "You", "Opponent"
        game.headers["White"], game.headers["Black"] = (
            (you, them) if self.user_color == chess.WHITE else (them, you)
        )
        pgn = str(game)
        self.games_dir.mkdir(parents=True, exist_ok=True)
        path = self.games_dir / f"{when:%Y%m%d-%H%M%S}.pgn"
        path.write_text(pgn + "\n")
        self.last_pgn_path = path
        return pgn


def _san_list(board: chess.Board) -> list[str]:
    replay = chess.Board()
    out = []
    for m in board.move_stack:
        out.append(replay.san(m))
        replay.push(m)
    return out


def _result_phrase(board: chess.Board) -> str:
    if board.is_checkmate():
        return "Checkmate"
    if board.is_stalemate():
        return "Stalemate"
    return "Draw"
