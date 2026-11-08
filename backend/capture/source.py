"""Common types for everything that produces camera frames."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import cv2
import numpy as np


@dataclass
class Frame:
    image: np.ndarray    # BGR
    t: float             # seconds: wall clock for live sources, media time for files
    index: int


class FrameSource(Protocol):
    paced: bool          # True: the worker should wait 1/fps between reads
    finished: bool       # True once a file source has no more frames

    def read(self) -> Frame | None:
        """Next frame, or None if there is none right now."""

    def close(self) -> None: ...


def simulate_call(image: np.ndarray, height: int = 720, jpeg_quality: int = 35) -> np.ndarray:
    """Degrade a sharp recording to roughly what a Messenger video call delivers."""
    h, w = image.shape[:2]
    if h > height:
        image = cv2.resize(image, (round(w * height / h), height), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
    return cv2.imdecode(buf, cv2.IMREAD_COLOR) if ok else image
