import chess
import numpy as np
import pytest

from backend import config
from backend.game.tracker import BLACK, EMPTY, WHITE, Tracker, candidates, occupancy_of
from backend.tests.conftest import probs_for


def after(board: chess.Board, *sans: str) -> chess.Board:
    b = board.copy()
    for san in sans:
        b.push_san(san)
    return b


def feed(tracker: Tracker, target, frames: int | None = None, **kw):
    """Show the same observation for STABLE_FRAMES frames; return the last result."""
    probs = probs_for(target, **kw)
    result = None
    for _ in range(frames or config.STABLE_FRAMES):
        result = tracker.observe(probs)
    return result


def feed_until_move(tracker: Tracker, target, max_frames: int = 20, **kw):
    """Fresh noise every frame; stop at the first accepted move (right or wrong)."""
    result = None
    for _ in range(max_frames):
        result = tracker.observe(probs_for(target, **kw))
        if result.status == "move":
            break
    return result


def assert_single_move(fen: str, san: str):
    board = chess.Board(fen)
    t = Tracker(board)
    result = feed(t, after(board, san))
    assert result.status == "move"
    assert [board.san(m) for m in result.moves] == [san]
    assert t.board.fen() == after(board, san).fen()


def test_occupancy_of_start():
    occ = occupancy_of(chess.Board())
    assert (occ[:16] == WHITE).all()
    assert (occ[16:48] == EMPTY).all()
    assert (occ[48:] == BLACK).all()


def test_normal_move_needs_stable_frames():
    t = Tracker()
    target = after(chess.Board(), "e4")
    probs = probs_for(target)
    statuses = [t.observe(probs).status for _ in range(config.STABLE_FRAMES)]
    assert statuses[:-1] == ["pending"] * (config.STABLE_FRAMES - 1)
    assert statuses[-1] == "move"
    assert t.board.move_stack == [chess.Move.from_uci("e2e4")]


def test_changing_observation_resets_stability():
    t = Tracker()
    e4 = probs_for(after(chess.Board(), "e4"))
    d4 = probs_for(after(chess.Board(), "d4"))
    for _ in range(config.STABLE_FRAMES - 1):
        t.observe(e4)
    assert t.observe(d4).status == "pending"
    assert not t.board.move_stack


def test_no_change():
    t = Tracker()
    assert feed(t, chess.Board()).status == "no_change"
    assert not t.board.move_stack


def test_capture():
    board = after(chess.Board(), "e4", "d5")
    assert_single_move(board.fen(), "exd5")


@pytest.mark.parametrize(
    "fen,san",
    [
        ("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1", "O-O"),
        ("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1", "O-O-O"),
        ("r3k2r/8/8/8/8/8/8/R3K2R b KQkq - 0 1", "O-O"),
        ("r3k2r/8/8/8/8/8/8/R3K2R b KQkq - 0 1", "O-O-O"),
    ],
)
def test_castling(fen, san):
    assert_single_move(fen, san)


def test_en_passant():
    board = after(chess.Board(), "e4", "a6", "e5", "d5")
    assert_single_move(board.fen(), "exd6")


def test_promotion_defaults_to_queen():
    assert_single_move("8/4P3/8/8/8/8/k7/4K3 w - - 0 1", "e8=Q")


def test_promotion_with_capture():
    assert_single_move("3r4/4P3/8/8/8/8/k7/4K3 w - - 0 1", "exd8=Q")


def test_impossible_grid_warns_once_then_recovers():
    t = Tracker()
    occ = occupancy_of(chess.Board())
    occ[chess.E2] = occ[chess.D2] = EMPTY      # two pawns vanished: matches nothing (one is tolerated)
    results = [feed(t, occ, frames=1).status for _ in range(config.BAD_LIMIT + 3)]
    assert results.count("check_board") == 1
    assert results[config.BAD_LIMIT - 1] == "check_board"
    assert set(results) == {"bad", "check_board"}
    assert not t.board.move_stack
    # Pawn is put back: board matches again, warning re-arms.
    assert feed(t, chess.Board()).status == "no_change"
    assert [feed(t, occ, frames=1).status for _ in range(config.BAD_LIMIT)][-1] == "check_board"


def test_two_ply_catch_up_after_looking_away():
    board = chess.Board()
    t = Tracker(board)
    result = feed(t, after(board, "e4", "c5"))
    assert result.status == "move"
    assert [m.uci() for m in result.moves] == ["e2e4", "c7c5"]
    assert t.board.turn == chess.WHITE


def test_one_ply_preferred_over_equivalent_two_ply():
    # No 2-ply candidate may shadow a 1-ply occupancy.
    board = chess.Board()
    keys = {}
    for c in candidates(board):
        assert c.key not in keys
        keys[c.key] = c
    one_ply = [c for c in keys.values() if len(c.moves) == 1]
    assert len(one_ply) == 20


def test_blended_observation_is_uncertain():
    t = Tracker()
    e3 = probs_for(after(chess.Board(), "e3"))
    e4 = probs_for(after(chess.Board(), "e4"))
    result = t.observe((e3 + e4) / 2)
    assert result.status == "uncertain"


def test_noisy_probabilities_still_track():
    rng = np.random.default_rng(42)
    board = chess.Board()
    t = Tracker(board)
    target = after(board, "Nf3")
    result = feed_until_move(t, target, conf=0.7, noise=0.15, rng=rng)
    assert result.status == "move"
    assert t.board.peek() == chess.Move.from_uci("g1f3")


def test_undo():
    t = Tracker()
    feed(t, after(chess.Board(), "e4"))
    assert t.undo() == chess.Move.from_uci("e2e4")
    assert t.board == chess.Board()
    assert t.undo() is None


def test_full_game_replay(opera_game):
    rng = np.random.default_rng(7)
    ref = chess.Board()
    t = Tracker()
    for san in opera_game:
        ref.push_san(san)
        result = feed_until_move(t, ref, conf=0.8, noise=0.1, rng=rng)
        assert result.status == "move", san
        assert t.board.fen() == ref.fen(), san
    assert t.board.is_checkmate()


def test_hidden_squares_are_neutral():
    """Squares out of frame (uniform probs) neither block nor fake a move."""
    hidden = [chess.square(7, r) for r in range(8)] + [chess.square(6, r) for r in range(8)]  # g+h files
    board = chess.Board()
    t = Tracker(board)
    target = after(board, "e4")
    probs = probs_for(target)
    probs[hidden] = 1 / 3
    result = None
    for _ in range(config.STABLE_FRAMES):
        result = t.observe(probs)
    assert result.status == "move" and t.board.peek() == chess.Move.from_uci("e2e4")

    # A move entirely on hidden squares is not guessed; it is caught once visible again.
    t2 = Tracker(after(board, "e4", "e5"))
    nf3 = after(board, "e4", "e5", "Nh3")
    probs = probs_for(nf3)
    probs[hidden] = 1 / 3
    assert all(t2.observe(probs).status != "move" for _ in range(5))
    assert feed(t2, nf3).status == "move"
    assert t2.board.peek() == chess.Move.from_uci("g1h3")
