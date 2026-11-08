"""Per-square features from the warped board, and a small classifier: empty / white / black.

The camera looks at the board from the near side and low, so in the warped
(top-down) image a piece's base sits in the lower part of its square and its
body leans into the square behind it. Features are therefore taken from a
"base" band (lower part) and a "body" band (upper part) of each square,
plus the base band of the square in front, which may hide this one.

The classifier is fit from the starting position at calibration and then
learns from every confirmed position, so it follows the room's lighting.
"""

from __future__ import annotations

from collections import deque

import cv2
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from backend.vision.board import CELL_PX

EMPTY, WHITE, BLACK = 0, 1, 2
# Regions inside a square, as fractions (x0, x1, y0, y1); y = 0 is the far edge.
BASE = (0.2, 0.8, 0.5, 0.95)
BODY = (0.25, 0.75, 0.1, 0.5)
MIN_VALID = 0.8          # a square counts as visible if this much of it is inside the frame
MAX_OCCLUDED = 0.12      # a square is hidden if more of its base band than this is a hand or sleeve
BUFFER_PER_CLASS = 800


def _region(img: np.ndarray, u: int, v: int, r) -> np.ndarray:
    px = CELL_PX
    y0 = (7 - v) * px
    x0 = u * px
    return img[y0 + int(r[2] * px): y0 + int(r[3] * px), x0 + int(r[0] * px): x0 + int(r[1] * px)]


def occluder_mask(warped: np.ndarray) -> np.ndarray:
    """Strongly coloured things on the board (sleeves, bright objects): neither the board's green nor a piece.

    Cream pieces under warm light reach saturation ~90 with skin-like hues, so skin is not
    masked here; a hand instead lowers the classifier's confidence and the frame is skipped.
    """
    hsv = cv2.cvtColor(warped, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    greenish = (h >= 35) & (h <= 95)
    return (s > 140) & (v > 60) & ~greenish


def square_features(warped: np.ndarray, valid: np.ndarray):
    """Returns (features (64, F), visible (64,) bool), indexed v * 8 + u (view order)."""
    lab = cv2.cvtColor(warped, cv2.COLOR_BGR2LAB).astype(np.float32)
    L = lab[..., 0] * (100 / 255)
    a = lab[..., 1] - 128
    b = lab[..., 2] - 128
    gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 60, 140) > 0
    occl = occluder_mask(warped)
    green = (np.abs(a + 12) < 9) & (L > 25) & (L < 75)     # the board's muted green
    # Brightness cut-offs follow the room's light: the light squares set the reference.
    ref = float(np.percentile(L[valid], 75)) if valid.any() else 90.0
    dark_cut, bright_cut = 0.4 * ref, 0.88 * ref

    def stats(u, v, r):
        if not 0 <= v <= 7:
            return [0.0] * 8
        Lr, ar, br = _region(L, u, v, r), _region(a, u, v, r), _region(b, u, v, r)
        return [
            float(Lr.mean()), float(Lr.std()), float(ar.mean()), float(br.mean()),
            float((Lr < dark_cut).mean()),                # dark: black pieces, shadows
            float((Lr > bright_cut).mean()),              # bright: white pieces, light squares
            float(_region(green, u, v, r).mean()),
            float(_region(edges, u, v, r).mean()),
        ]

    feats, visible = [], []
    for v in range(8):
        for u in range(8):
            dark_square = (u + v) % 2 == 0
            f = [1.0 if dark_square else 0.0]
            f += stats(u, v, BASE) + stats(u, v, BODY) + stats(u, v - 1, BASE)
            feats.append(f)
            vis = _region(valid, u, v, (0, 1, 0, 1)).mean() >= MIN_VALID
            vis &= _region(occl, u, v, BASE).mean() <= MAX_OCCLUDED
            visible.append(bool(vis))
    return np.array(feats, np.float64), np.array(visible)


class SquareModel:
    """Logistic regression over square features, refit as labelled frames arrive."""

    def __init__(self):
        self._data: dict[int, deque] = {c: deque(maxlen=BUFFER_PER_CLASS) for c in (EMPTY, WHITE, BLACK)}
        self._clf = None

    @property
    def ready(self) -> bool:
        return self._clf is not None

    def add(self, feats: np.ndarray, labels: np.ndarray, mask: np.ndarray | None = None) -> None:
        mask = np.ones(len(labels), bool) if mask is None else mask
        for f, y in zip(feats[mask], labels[mask]):
            self._data[int(y)].append(f)

    def fit(self) -> bool:
        if any(len(d) < 4 for d in self._data.values()):
            return False
        X = np.concatenate([np.array(d) for d in self._data.values()])
        y = np.concatenate([np.full(len(d), c) for c, d in self._data.items()])
        clf = make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=500))
        clf.fit(X, y)
        self._clf = clf
        return True

    def predict(self, feats: np.ndarray) -> np.ndarray:
        """(64, 3) probabilities [empty, white, black]."""
        p = self._clf.predict_proba(feats)
        out = np.zeros((len(feats), 3))
        out[:, self._clf.classes_] = p
        return out
