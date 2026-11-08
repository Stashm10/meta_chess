"""What the vision step returns for one frame. The real implementation is `vision/reader.py`."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from backend.capture.source import Frame


@dataclass
class VisionResult:
    probs: np.ndarray                 # (64, 3) [empty, white, black], a1 first; hidden squares uniform
    confidence: float
    warped: np.ndarray | None = None  # top-down board image for the dashboard
    user_color: bool | None = None    # chess.WHITE / chess.BLACK once calibration has decided


def no_vision(frame: Frame) -> VisionResult | None:
    """Phase 3 stub: never sees a board, so the game never reacts to real footage."""
    return None
