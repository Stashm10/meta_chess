import chess
import numpy as np
import pytest

from backend import config
from backend.game.speech import move_to_speech
from backend.loop import CALIBRATING, CHECK_BOARD, GAME_OVER, IDLE, WAITING, YOUR_MOVE, GameSession
from backend.game.tracker import EMPTY, occupancy_of
from backend.tests.conftest import probs_for


class FakeSpeaker:
    def __init__(self):
        self.spoken: list[str] = []
        self.last_text = None

    def say(self, text, force=False):
        if text == self.last_text and not force:
            return False
        self.last_text = text
        self.spoken.append(text)
        return True

    def repeat(self):
        return self.say(self.last_text, force=True) if self.last_text else False


class ScriptedEngine:
    """Plays moves from a SAN script when it matches the position, else the first legal move."""

    def __init__(self, script: list[str] | None = None):
        self.script = script or []
        self.calls = 0

    def best_move(self, board: chess.Board):
        self.calls += 1
        if board.is_game_over():
            return None
        ply = len(board.move_stack)
        if ply < len(self.script):
            return board.parse_san(self.script[ply])
        return next(iter(board.legal_moves))


@pytest.fixture
def make_session(tmp_path):
    def make(script=None):
        speaker, engine = FakeSpeaker(), ScriptedEngine(script)
        return GameSession(engine, speaker, games_dir=tmp_path), speaker, engine
    return make


def show(session, target, frames=None, **kw):
    for _ in range(frames or config.STABLE_FRAMES):
        status = session.observe(probs_for(target, **kw))
    return status


def test_idle_ignores_frames(make_session):
    s, speaker, _ = make_session()
    assert show(s, chess.Board()) == IDLE
    assert speaker.spoken == []


def test_calibrate_as_white_speaks_lock_then_best_move(make_session):
    s, speaker, _ = make_session(["e4"])
    s.new_game()
    assert s.status == CALIBRATING
    assert show(s, chess.Board()) == YOUR_MOVE
    assert speaker.spoken == ["Board locked, you are White", "Pawn to e4"]
    assert s.state()["best_move"] == "e4"


def test_calibration_needs_starting_position(make_session):
    s, speaker, _ = make_session()
    s.new_game()
    b = chess.Board()
    b.push_san("e4")
    assert show(s, b, frames=10) == CALIBRATING
    assert speaker.spoken == []


def test_as_black_waits_for_opponent(make_session):
    s, speaker, _ = make_session(["e4", "c5"])
    s.new_game(user_color=chess.BLACK)
    assert show(s, chess.Board()) == WAITING
    assert speaker.spoken == ["Board locked, you are Black"]
    b = chess.Board()
    b.push_san("e4")
    assert show(s, b) == YOUR_MOVE
    assert speaker.spoken[-1] == "Pawn to c5"


def test_low_confidence_frames_are_skipped(make_session):
    s, speaker, _ = make_session()
    s.new_game()
    for _ in range(10):
        s.observe(probs_for(chess.Board()), confidence=config.MIN_CONF - 0.1)
    assert s.status == CALIBRATING


def test_user_plays_different_move_than_suggested(make_session):
    s, speaker, engine = make_session(["e4", "e5", "Nf3"])
    s.new_game()
    show(s, chess.Board())
    b = chess.Board()
    b.push_san("d4")                      # user ignores the suggestion
    assert show(s, b) == WAITING
    b.push_san("d5")
    assert show(s, b) == YOUR_MOVE
    assert s.state()["moves"] == ["d4", "d5"]
    assert speaker.spoken[-1] == move_to_speech(b, engine.best_move(b))


def test_check_board_once_then_recovers(make_session):
    s, speaker, _ = make_session(["e4"])
    s.new_game()
    show(s, chess.Board())
    occ = occupancy_of(chess.Board())
    occ[chess.A2] = occ[chess.B2] = EMPTY      # one misread square is tolerated; two are not
    for _ in range(config.BAD_LIMIT + 5):
        s.observe(probs_for(occ))
    assert s.status == CHECK_BOARD
    assert speaker.spoken.count("Check the board") == 1
    show(s, chess.Board())
    assert s.status == YOUR_MOVE
    assert not s.state()["moves"]


def test_undo_resuggests(make_session):
    s, speaker, _ = make_session(["e4", "e5"])
    s.new_game()
    show(s, chess.Board())
    b = chess.Board()
    b.push_san("e4")
    show(s, b)
    assert s.status == WAITING
    assert s.undo() == "e2e4"
    assert s.status == YOUR_MOVE
    assert speaker.spoken[-1] == "Pawn to e4"


def test_repeat(make_session):
    s, speaker, _ = make_session(["e4"])
    s.new_game()
    show(s, chess.Board())
    assert s.repeat()
    assert speaker.spoken[-2:] == ["Pawn to e4", "Pawn to e4"]


def test_no_board_is_silent(make_session):
    s, speaker, _ = make_session()
    s.new_game()
    s.observe_no_board()
    assert s.state()["board_visible"] is False
    assert speaker.spoken == []


def test_end_game_saves_pgn(make_session, tmp_path):
    s, _, _ = make_session(["e4"])
    s.new_game()
    show(s, chess.Board())
    b = chess.Board()
    b.push_san("e4")
    show(s, b)
    pgn = s.end_game()
    assert "1. e4" in pgn
    assert s.status == IDLE
    assert list(tmp_path.glob("*.pgn"))


def test_state_is_json_friendly(make_session):
    import json
    s, _, _ = make_session()
    s.new_game()
    show(s, chess.Board())
    json.dumps(s.state())


def test_full_game_speaks_every_user_turn(make_session, opera_game, tmp_path):
    """Opera Game as White through noisy fake vision: every user turn gets the scripted move."""
    s, speaker, _ = make_session(opera_game)
    rng = np.random.default_rng(3)
    s.new_game()
    for _ in range(config.STABLE_FRAMES):
        s.observe(probs_for(chess.Board(), conf=0.85, noise=0.1, rng=rng))

    ref = chess.Board()
    expected = ["Board locked, you are White"]
    for ply, san in enumerate(opera_game):
        if ref.turn == chess.WHITE:
            expected.append(move_to_speech(ref, ref.parse_san(san)))
        ref.push_san(san)
        for _ in range(20):
            s.observe(probs_for(ref, conf=0.85, noise=0.1, rng=rng))
            if s.state()["fen"] == ref.fen():
                break
        assert s.state()["fen"] == ref.fen(), f"stuck after {san}"

    expected.append("Checkmate")
    assert speaker.spoken == expected
    assert s.status == GAME_OVER
    pgn = (list(tmp_path.glob("*.pgn"))[0]).read_text()
    assert "Rd8#" in pgn and "1-0" in pgn
