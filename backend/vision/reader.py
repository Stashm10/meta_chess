"""The vision step: frame -> per-square [empty, white, black] probabilities.

Calibration needs no labels: games always start from the starting position,
so the first clear view of a board whose near two rows and far two rows are
occupied labels all 64 squares. The colour of the near rows is the user's
side. After that, every confirmed position labels the frame it was seen in,
and the classifier is refit.
"""

from __future__ import annotations

import logging
import threading

import chess
import cv2
import numpy as np

from backend import config
from backend.capture.source import Frame
from backend.game.tracker import occupancy_of
from backend.vision.base import VisionResult
from backend.vision.board import find_board
from backend.vision.squares import BLACK, EMPTY, WHITE, SquareModel, square_features

log = logging.getLogger(__name__)

MAX_SIDE = 1280             # frames are downscaled to this long side first (the phone already sends 1280)
UNIFORM = np.full(3, 1 / 3)
# Feature columns (see squares.square_features): [dark square, base(8), body(8), front base(8)]
F_BASE_GREEN, F_BASE_EDGE = 7, 8
F_BASE_DARK, F_BODY_DARK = 5, 13


def view_to_square(u: int, v: int, user_color: chess.Color) -> chess.Square:
    """View cell (u from the left, v from the near side) -> real square."""
    if user_color == chess.WHITE:
        return chess.square(u, v)
    return chess.square(7 - u, 7 - v)


def start_labels_view() -> np.ndarray:
    """Starting position in view order, as [empty, mine, theirs] by row; colours resolved later."""
    rows = np.array([1, 1, 0, 0, 0, 0, 2, 2])
    return np.repeat(rows, 8)


class BoardReader:
    def __init__(self):
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self.model = SquareModel()
        self.user_color: chess.Color | None = None
        self.last_feats: np.ndarray | None = None
        self.last_visible: np.ndarray | None = None
        self.last_board = None
        self.last_size: tuple[int, int] | None = None
        self.relearn: chess.Board | None = None     # set by Recalibrate: learn from this position, not the start
        self._why("waiting for the starting position", "Look at the starting position")

    def _why(self, note: str, hint: str | None) -> None:
        """`note` is for the dashboard; `hint` is the short advice the phone shows."""
        self.note, self.hint = note, hint

    # ---------- frames ----------

    def __call__(self, frame: Frame) -> VisionResult | None:
        image = frame.image
        h, w = image.shape[:2]
        scale = MAX_SIDE / max(h, w)
        if scale < 1:
            image = cv2.resize(image, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)

        self.last_size = image.shape[1::-1]
        board = find_board(image)
        if board is None or board.quality < config.MIN_BOARD_QUALITY:
            self.last_board = None
            if board is None:
                self._why("no board in view", "Look at the board")
            else:
                self._why(f"board unclear (quality {board.quality:.2f})", "Sit closer and hold still")
            return None
        warped, valid = board.warp(image)
        feats, visible = square_features(warped, valid)
        if visible.sum() < config.MIN_VISIBLE_SQUARES:
            self.last_board = board          # still drawn on the dashboard, to show what is cut off
            self._why(f"only {visible.sum()} squares in view", "Get the whole board in view")
            return None

        with self._lock:
            self.last_feats, self.last_visible, self.last_board = feats, visible, board
            if not self.model.ready:
                learned = self._relearn(feats, visible) if self.relearn is not None else self._bootstrap(feats, visible)
                if not learned:
                    return None
            else:
                self._why(f"reading the board ({visible.sum()} squares in view)", None)
            view = self.model.predict(feats)
            view[~visible] = UNIFORM
            color = self.user_color

        probs = np.empty((64, 3))
        for v in range(8):
            for u in range(8):
                probs[view_to_square(u, v, color)] = view[v * 8 + u]
        confidence = float(view[visible].max(axis=1).mean())
        return VisionResult(probs, confidence, warped, user_color=color)

    def _bootstrap(self, feats: np.ndarray, visible: np.ndarray) -> bool:
        """Fit the classifier if this frame looks like the starting position."""
        if visible.sum() < 56:
            self._why(f"starting position: only {visible.sum()} squares in view", "Get the whole board in view")
            return False
        rows = start_labels_view()
        # Judge emptiness on the green squares only: any piece hides green, while a cream
        # piece on a cream square looks almost like an empty one.
        green_square = feats[:, 0] > 0.5
        plain = feats[:, F_BASE_GREEN] - 2 * feats[:, F_BASE_EDGE]
        occupied = rows > 0
        # The third row from you is empty but sits behind your pieces, whose bodies lean over it.
        # Dark (black) pieces make it look anything but plain, so judge emptiness on the three rows after it.
        row = np.repeat(np.arange(8), 8)
        clear = ~occupied & (row >= 3)
        a, b = plain[visible & green_square & clear], plain[visible & green_square & occupied]
        if len(a) < 8 or len(b) < 10:
            self._why("starting position: too few squares in view", "Get the whole board in view")
            return False
        separation = (a[:, None] > b[None, :]).mean()          # rank AUC: empty looks plainer
        if separation < 0.92 or a.mean() - b.mean() < 0.4:
            self._why(f"not the starting position (separation {separation:.2f}, contrast {a.mean() - b.mean():.2f})",
                      "Set up the starting position")
            return False

        darkness = feats[:, F_BASE_DARK] + feats[:, F_BODY_DARK]
        near, far = darkness[visible & (rows == 1)].mean(), darkness[visible & (rows == 2)].mean()
        if abs(near - far) < 0.3:
            self._why(f"can't tell which side is yours (near {near:.2f}, far {far:.2f})", "Look down the board from your side")
            return False
        self.user_color = chess.WHITE if near < far else chess.BLACK
        mine, theirs = (WHITE, BLACK) if self.user_color == chess.WHITE else (BLACK, WHITE)
        labels = np.choose(rows, [EMPTY, mine, theirs])
        self.model.add(feats, labels, visible)
        ok = self.model.fit()
        self._why(f"calibrated: you are {'White' if self.user_color else 'Black'}", None)
        log.info("vision %s", self.note)
        return ok

    def _relearn(self, feats: np.ndarray, visible: np.ndarray) -> bool:
        """After Recalibrate: label this frame with the confirmed position and refit."""
        if visible.sum() < 56:
            self._why(f"recalibrating: only {visible.sum()} squares in view", "Get the whole board in view")
            return False
        board, self.relearn = self.relearn, None
        self.last_feats, self.last_visible = feats, visible
        self._learn(board)
        if not self.model.ready:
            self.relearn = board
            return False
        self._why("recalibrated from the current position", None)
        log.info("vision %s", self.note)
        return True

    def draw(self, image: np.ndarray) -> np.ndarray:
        """The board grid vision found in the latest frame, drawn over `image` (any size) for the dashboard."""
        board, size = self.last_board, self.last_size
        if board is None or size is None:
            return image
        out = image.copy()
        k = out.shape[1] / size[0]
        t = np.linspace(0, 8, 33)
        for i in range(9):
            for uv, near in ((np.stack([np.full_like(t, i), t], 1), False), (np.stack([t, np.full_like(t, i)], 1), i == 0)):
                pts = (board.to_image(uv) * k).round().astype(np.int32)
                color = (0, 140, 255) if near else (80, 255, 80)      # your edge of the board in orange
                cv2.polylines(out, [pts], False, color, 3 if near else 1, cv2.LINE_AA)
        return out

    # ---------- learning from the game ----------

    def on_event(self, kind: str, data: dict) -> None:
        """GameSession listener: new games reset, confirmed positions teach the classifier."""
        with self._lock:
            if kind in ("new_game", "end"):
                self.reset()
                return
            if kind == "recalibrate":
                # Forget what the squares look like (the light changed, the glasses moved). Mid-game,
                # relearn from the confirmed position; before the lock, from the starting position.
                color, fen = self.user_color, data.get("fen")
                self.reset()
                if fen and color is not None:
                    self.user_color, self.relearn = color, chess.Board(fen)
                    self._why("recalibrating from the current position", "Hold still on the board")
                return
            if kind == "lock":
                wanted = chess.WHITE if data.get("user_color") == "White" else chess.BLACK
                if self.user_color is not None and wanted != self.user_color:
                    # The dashboard overrode the detected side: relearn with the other orientation.
                    self.reset()
                self.user_color = wanted
            if kind in ("lock", "move", "undo") and data.get("fen") and self.last_feats is not None:
                self._learn(chess.Board(data["fen"]))

    def _learn(self, board: chess.Board) -> None:
        if self.user_color is None:
            return
        occ = occupancy_of(board)                # by real square: 0 empty, 1 white, 2 black
        labels = np.array([occ[view_to_square(u, v, self.user_color)] for v in range(8) for u in range(8)])
        self.model.add(self.last_feats, labels, self.last_visible)
        self.model.fit()
