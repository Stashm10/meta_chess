"""Vision on the user's board photos (iPhone stills of the starting position, downscaled to glasses size)."""

from pathlib import Path

import chess
import cv2
import numpy as np
import pytest

from backend.capture.source import Frame
from backend.game.tracker import occupancy_of
from backend.vision.board import find_board
from backend.vision.reader import BoardReader, view_to_square

PHOTOS = sorted((Path(__file__).parent / "fixtures" / "photos").glob("*.jpg"))
START = occupancy_of(chess.Board())


def load(path, height=720):
    img = cv2.imread(str(path))
    h, w = img.shape[:2]
    return cv2.resize(img, (round(w * height / h), height), interpolation=cv2.INTER_AREA)


@pytest.fixture(scope="module")
def photos():
    assert PHOTOS, "board photos missing"
    return [load(p) for p in PHOTOS]


def test_board_found_in_every_photo(photos):
    for img in photos:
        board = find_board(img)
        assert board is not None
        assert board.visible.sum() >= 60
        assert board.quality >= 0.5
        # Parity: the near-left square is dark, so a3 (empty at the start) is green.
        x, y = board.to_image(np.array([[0.5, 2.5]]))[0].astype(int)
        hue, sat, _ = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)[y, x]
        assert 35 <= hue <= 95 and sat > 25


def test_no_board_in_a_blank_frame():
    assert find_board(np.full((720, 540, 3), 200, np.uint8)) is None
    noise = np.random.default_rng(1).integers(0, 255, (720, 540, 3), dtype=np.uint8)
    assert find_board(noise) is None


def test_reader_calibrates_from_the_starting_position(photos):
    for img in photos:
        reader = BoardReader()
        result = reader(Frame(img, 0.0, 0))
        assert result is not None
        assert result.user_color == chess.WHITE
        assert (result.probs.argmax(axis=1) == START).sum() >= 60
        assert result.confidence > 0.9


def test_reader_sees_black_when_the_board_is_turned(photos):
    turned = cv2.rotate(photos[-1], cv2.ROTATE_180)
    result = BoardReader()(Frame(turned, 0.0, 0))
    assert result is not None and result.user_color == chess.BLACK
    # Output is still in real squares: white pieces on ranks 1-2.
    assert (result.probs.argmax(axis=1) == START).sum() >= 60


def test_reader_learns_from_confirmed_positions(photos):
    reader = BoardReader()
    reader(Frame(photos[-1], 0.0, 0))
    before = len(reader.model._data[0])
    reader.on_event("lock", {"user_color": "White", "fen": chess.Board().fen()})
    assert len(reader.model._data[0]) > before
    reader.on_event("new_game", {})
    assert not reader.model.ready and reader.user_color is None


def test_view_to_square():
    assert view_to_square(0, 0, chess.WHITE) == chess.A1
    assert view_to_square(7, 7, chess.WHITE) == chess.H8
    assert view_to_square(0, 0, chess.BLACK) == chess.H8
    assert view_to_square(3, 1, chess.BLACK) == chess.E7


GLASSES_VIDEO = Path(__file__).resolve().parents[2] / "recordings" / "glasses-test" / "game1.mov"


@pytest.mark.skipif(not GLASSES_VIDEO.exists(), reason="glasses test video not present (gitignored)")
def test_glasses_video_opening_is_tracked():
    """The first 40 s of the user's glasses recording: lock as White, then the Queen's Gambit moves."""
    from tools.eval_video import replay

    out = replay(str(GLASSES_VIDEO), until=40)
    assert out["statuses"][1][1] == "your_move" and out["statuses"][1][0] < 2
    assert out["moves"][:10] == ["d4", "d5", "c4", "e6", "Nf3", "Nf6", "Bg5", "h6", "Bh4", "g5"]


def test_recalibrate_relearns_from_the_current_position(photos):
    reader = BoardReader()
    reader(Frame(photos[-1], 0.0, 0))
    reader.on_event("recalibrate", {"fen": chess.Board().fen()})
    assert not reader.model.ready and reader.user_color == chess.WHITE and reader.hint
    result = reader(Frame(photos[-1], 0.0, 1))
    assert result is not None and reader.model.ready and reader.hint is None
    assert (result.probs.argmax(axis=1) == START).sum() >= 60


def test_hint_when_no_board_and_grid_overlay(photos):
    reader = BoardReader()
    assert reader(Frame(np.full((720, 540, 3), 200, np.uint8), 0.0, 0)) is None
    assert reader.hint == "Look at the board"
    reader(Frame(photos[-1], 0.0, 1))
    small = cv2.resize(photos[-1], None, fx=0.5, fy=0.5)
    assert (reader.draw(small) != small).any()
