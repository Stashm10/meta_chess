"""One place that owns the active capture: source, worker, recording, keep-awake."""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path

from backend import config
from backend.capture.file_source import FileSource
from backend.capture.phone_source import PhoneSource
from backend.capture.recorder import Recorder
from backend.capture.source import Frame
from backend.capture.window import Region, RegionSource
from backend.capture.worker import CaptureWorker
from backend.vision.base import VisionResult
from backend.vision.reader import BoardReader

log = logging.getLogger(__name__)


class CaptureManager:
    def __init__(
        self,
        session,
        vision: Callable[[Frame], VisionResult | None] | None = None,
        recordings_dir: Path | None = None,
        keep_awake: bool = True,
        source_factory: Callable[[dict], object] | None = None,
    ):
        self.session = session
        if vision is None:
            # The board reader learns from confirmed positions, so it listens to the game.
            vision = BoardReader()
            session.listeners.append(vision.on_event)
        self.vision = vision
        self.recordings_dir = recordings_dir or config.RECORDINGS_DIR
        self.keep_awake = keep_awake
        self.source_factory = source_factory or make_source
        self.worker: CaptureWorker | None = None
        self.recorder: Recorder | None = None
        self.source_kind: str | None = None
        self.last_error: str | None = None
        self._caffeinate: subprocess.Popen | None = None
        self._lock = threading.RLock()
        if config.RECORD:
            self.set_recording(True)

    def start(self, spec: dict) -> dict:
        """spec: {source: window|region|file|phone, region?: [x,y,w,h], path?, realtime?, call_quality?, loop?}"""
        with self._lock:
            self.stop()
            source = self.source_factory(spec)       # raises on a bad spec / unreadable file
            self.source_kind = spec.get("source")
            self.last_error = None
            self.worker = CaptureWorker(source, self.session, self.vision, recorder=self.recorder)
            self.worker.start()
            self._awake(True)
            return self.status()

    def stop(self) -> dict:
        with self._lock:
            if self.worker is not None:
                self.worker.stop()
                self.last_error = self.worker.error
                self.worker = None
            self._awake(False)
            return self.status()

    def set_recording(self, on: bool) -> str | None:
        with self._lock:
            if on and self.recorder is None:
                self.recorder = Recorder(self.recordings_dir)
                self.session.listeners.append(self.recorder.event)
            elif not on and self.recorder is not None:
                if self.recorder.event in self.session.listeners:
                    self.session.listeners.remove(self.recorder.event)
                self.recorder.close()
                self.recorder = None
            if self.worker is not None:
                self.worker.recorder = self.recorder
            return str(self.recorder.dir) if self.recorder else None

    @property
    def phone(self) -> PhoneSource | None:
        """The active phone source, if the phone is the capture source."""
        w = self.worker
        return w.source if w is not None and isinstance(w.source, PhoneSource) else None

    def latest_jpeg(self) -> bytes | None:
        return self.worker.latest_jpeg() if self.worker else None

    def status(self) -> dict:
        w = self.worker
        return {
            "source": self.source_kind if w else None,
            "running": bool(w and w.running),
            "ended": bool(w and w.ended),
            "frames": w.frames if w else 0,
            "fps_actual": round(w.fps_actual, 2) if w else 0.0,
            "frame_size": w.frame_size if w else None,
            "error": (w.error if w else None) or self.last_error,
            "vision": getattr(self.vision, "note", None),
            "recording": str(self.recorder.dir) if self.recorder else None,
            "recorded_frames": self.recorder.frames_written if self.recorder else 0,
        }

    def shutdown(self) -> None:
        self.stop()
        self.set_recording(False)

    def _awake(self, on: bool) -> None:
        """Keep the display awake while capturing (the call window must stay visible)."""
        if not self.keep_awake:
            return
        if on and self._caffeinate is None:
            try:
                self._caffeinate = subprocess.Popen(["caffeinate", "-d", "-i", "-w", str(os.getpid())])
            except OSError:
                log.warning("caffeinate not available; the display may sleep")
        elif not on and self._caffeinate is not None:
            self._caffeinate.terminate()
            self._caffeinate = None


def make_source(spec: dict):
    kind = spec.get("source")
    if kind == "window":
        return RegionSource()
    if kind == "region":
        x, y, w, h = (int(v) for v in spec["region"])
        return RegionSource(Region(x, y, w, h))
    if kind == "phone":
        return PhoneSource()
    if kind == "file":
        return FileSource(
            spec["path"],
            realtime=spec.get("realtime", True),
            call_quality=spec.get("call_quality", False),
            loop=spec.get("loop", False),
        )
    raise ValueError(f"unknown source {kind!r}")
