"""Frames uploaded by the iPhone app (glasses camera via Meta's DAT SDK).

A single slot holds the newest frame: each upload replaces it, nothing is queued,
so a slow vision step only ever sees the latest view.
"""

from __future__ import annotations

import threading
import time

import cv2
import numpy as np

from backend.capture.source import Frame

READ_TIMEOUT = 0.5      # seconds the worker waits for a new upload before checking stop
STALE_SECONDS = 3.0     # no upload for this long = "phone not sending"


def decode_jpeg(data: bytes) -> np.ndarray:
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR) if data else None
    if image is None:
        raise ValueError("not a JPEG image")
    return image


class PhoneSource:
    paced = False          # the phone paces uploads; read() blocks until one arrives
    finished = False

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.device: str | None = None
        self.last_seen: float | None = None
        self.uploads = 0
        self._slot: Frame | None = None
        self._index = 0
        self._cond = threading.Condition()
        self._closed = False

    def push(self, jpeg: bytes) -> None:
        """Store an uploaded frame. Raises ValueError if it is not a decodable JPEG."""
        image = decode_jpeg(jpeg)
        with self._cond:
            self._slot = Frame(image, time.time(), self._index)
            self._index += 1
            self.uploads += 1
            self.last_seen = self.clock()
            self._cond.notify_all()

    def read(self) -> Frame | None:
        with self._cond:
            if self._slot is None and not self._closed:
                self._cond.wait(READ_TIMEOUT)
            frame, self._slot = self._slot, None
            return frame

    @property
    def error(self) -> str | None:
        if self.last_seen is None:
            return "Waiting for the phone to send frames"
        if self.clock() - self.last_seen > STALE_SECONDS:
            return "Phone not sending frames"
        return None

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()
