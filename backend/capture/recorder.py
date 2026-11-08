"""Save frames plus game events so a session can be replayed and auto-labeled later.

Layout: <dir>/frames/000123.jpg and <dir>/events.jsonl, one JSON object per line:
  {"kind": "frame", "frame": 123, "t": 41.0, "fen": "<confirmed FEN or null>", "board": true}
  {"kind": "move", "frame": 123, "t": 41.2, "uci": ["e2e4"], "fen": "..."}
  also "new_game", "lock", "undo", "end".
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path

import cv2

from backend import config
from backend.capture.source import Frame

JPEG_QUALITY = 90


class Recorder:
    def __init__(self, root: Path | None = None, name: str | None = None):
        root = root or config.RECORDINGS_DIR
        self.dir = root / (name or datetime.now().strftime("%Y%m%d-%H%M%S"))
        (self.dir / "frames").mkdir(parents=True, exist_ok=True)
        self._events = open(self.dir / "events.jsonl", "a", buffering=1)
        self._lock = threading.Lock()
        self._frame = -1
        self._t0 = time.monotonic()
        self.frames_written = 0

    def frame(self, frame: Frame, fen: str | None, board_seen: bool) -> None:
        with self._lock:
            if self._events.closed:
                return
            self._frame += 1
            cv2.imwrite(str(self.dir / "frames" / f"{self._frame:06d}.jpg"), frame.image,
                        [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
            self._write({"kind": "frame", "frame": self._frame, "t": frame.t, "fen": fen, "board": board_seen})
            self.frames_written += 1

    def event(self, kind: str, data: dict) -> None:
        """Game-session listener: (kind, data)."""
        with self._lock:
            if self._events.closed:
                return
            self._write({"kind": kind, "frame": self._frame, "t": time.monotonic() - self._t0, **data})

    def _write(self, record: dict) -> None:
        self._events.write(json.dumps(record) + "\n")

    def close(self) -> None:
        with self._lock:
            if not self._events.closed:
                self._events.close()
