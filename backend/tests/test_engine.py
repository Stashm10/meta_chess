from pathlib import Path

import chess
import pytest

from backend import config
from backend.game.engine import Engine, EngineUnavailable

needs_stockfish = pytest.mark.skipif(
    not Path(config.STOCKFISH_PATH).is_file(), reason="Stockfish not installed"
)


@pytest.fixture(scope="module")
def engine():
    with Engine(think_time=0.1) as e:
        yield e


def test_missing_binary_raises():
    with pytest.raises(EngineUnavailable):
        Engine(path="/nonexistent/stockfish").best_move(chess.Board())


def test_game_over_returns_none_without_starting():
    board = chess.Board("rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3")
    assert board.is_checkmate()
    assert Engine(path="/nonexistent/stockfish").best_move(board) is None


@needs_stockfish
def test_best_move_is_legal(engine):
    board = chess.Board()
    move = engine.best_move(board)
    assert move in board.legal_moves


@needs_stockfish
def test_finds_mate_in_one(engine):
    board = chess.Board("6k1/5ppp/8/8/8/8/8/R5K1 w - - 0 1")
    assert engine.best_move(board) == chess.Move.from_uci("a1a8")
