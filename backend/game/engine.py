"""Persistent Stockfish process."""

from __future__ import annotations

from pathlib import Path

import chess
import chess.engine

from backend import config


class EngineUnavailable(RuntimeError):
    pass


class Engine:
    """One Stockfish process, started lazily and reused for every search."""

    def __init__(self, path: str | None = None, think_time: float | None = None):
        self.path = path or config.STOCKFISH_PATH
        self.think_time = think_time if think_time is not None else config.ENGINE_TIME
        self._engine: chess.engine.SimpleEngine | None = None

    def start(self) -> None:
        if self._engine is not None:
            return
        if not Path(self.path).is_file():
            raise EngineUnavailable(f"Stockfish not found at {self.path}; set STOCKFISH_PATH")
        self._engine = chess.engine.SimpleEngine.popen_uci(self.path)

    def best_move(self, board: chess.Board) -> chess.Move | None:
        """Best move for the side to move, or None if the game is over."""
        if board.is_game_over():
            return None
        self.start()
        result = self._engine.play(board, chess.engine.Limit(time=self.think_time))
        return result.move

    def close(self) -> None:
        if self._engine is not None:
            self._engine.quit()
            self._engine = None

    def __enter__(self) -> Engine:
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()
