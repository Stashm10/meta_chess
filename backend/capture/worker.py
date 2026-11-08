"""Background loop: frame source → vision → game session (plus optional recording)."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

import cv2
import numpy as np

from backend import config
from backend.capture.recorder import Recorder
from backend.capture.source import Frame, FrameSource
from backend.vision.base import VisionResult

log = logging.getLogger(__name__)

PREVIEW_MAX_SIDE = 960


class CaptureWorker:
    def __init__(
        self,
        source: FrameSource,
        session,
        vision: Callable[[Frame], VisionResult | None],
        fps: float | None = None,
        recorder: Recorder | None = None,
    ):
        self.source = source
        self.session = session
        self.vision = vision
        self.fps = fps or config.CAPTURE_FPS
        self.recorder = recorder
        self.frames = 0
        self.fps_actual = 0.0
        self.error: str | None = None
        self.ended = False
        self._latest: Frame | None = None
        self._latest_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---------- lifecycle ----------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="capture", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        self.source.close()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def wait(self, timeout: float | None = None) -> None:
        """Block until a finite source has been fully processed (tests)."""
        if self._thread is not None:
            self._thread.join(timeout)

    # ---------- frames ----------

    def latest_jpeg(self) -> bytes | None:
        with self._latest_lock:
            frame = self._latest
        if frame is None:
            return None
        image = frame.image
        h, w = image.shape[:2]
        scale = PREVIEW_MAX_SIDE / max(h, w)
        if scale < 1:
            image = cv2.resize(image, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
        if hasattr(self.vision, "draw"):
            image = self.vision.draw(image)
        ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return buf.tobytes() if ok else None

    @property
    def frame_size(self) -> list[int] | None:
        frame = self._latest
        return None if frame is None else [frame.image.shape[1], frame.image.shape[0]]

    def _run(self) -> None:
        interval = 1.0 / self.fps
        last = None
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                frame = self.source.read()
            except Exception as e:                       # a broken source must not kill the server
                log.exception("frame source failed")
                self.error = str(e)
                break
            self.error = getattr(self.source, "error", None)

            if frame is None:
                if self.source.finished:
                    self.ended = True
                    break
                self._stop.wait(min(interval, 0.5))
                continue

            with self._latest_lock:
                self._latest = frame
            self.frames += 1
            arrived = time.monotonic()        # after read(): file sources sleep inside it
            if last is not None:
                dt = arrived - last
                if dt > 0:
                    self.fps_actual = 0.8 * self.fps_actual + 0.2 / dt if self.fps_actual else 1 / dt
            last = arrived

            self._process(frame)

            if self.source.paced:
                self._stop.wait(max(0.0, interval - (time.monotonic() - started)))

    def _process(self, frame: Frame) -> None:
        try:
            result = self.vision(frame)
        except Exception:
            log.exception("vision failed")
            result = None
        if result is None:
            self.session.observe_no_board()
        else:
            self.session.observe(np.asarray(result.probs), result.confidence, result.user_color)
        if self.recorder is not None:
            self.recorder.frame(frame, self.session.confirmed_fen(), result is not None)
