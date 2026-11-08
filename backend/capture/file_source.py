"""Replay a recorded video or a folder of images as if it were the live call."""

from __future__ import annotations

import time
from pathlib import Path

import cv2

from backend import config
from backend.capture.source import Frame, simulate_call

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp"}
VIDEO_EXTS = {".mov", ".mp4", ".m4v", ".avi", ".mkv"}


class SourceError(RuntimeError):
    pass


class FileSource:
    """Frames from a video (sampled at `fps` of media time) or an image folder (sorted).

    realtime=True plays at real speed and skips frames it fell behind on, like a live
    stream. realtime=False returns every sampled frame as fast as it is asked for.
    """

    def __init__(
        self,
        path: str | Path,
        fps: float | None = None,
        loop: bool = False,
        realtime: bool = False,
        call_quality: bool = False,
    ):
        self.path = Path(path).expanduser()
        self.fps = fps or config.CAPTURE_FPS
        self.loop = loop
        self.realtime = realtime
        self.call_quality = call_quality
        self.paced = False                   # pacing is handled here, not by the worker
        self.finished = False
        self._index = 0
        self._started: float | None = None

        if self.path.is_dir():
            self._images = sorted(p for p in self.path.iterdir() if p.suffix.lower() in IMAGE_EXTS)
            if not self._images:
                raise SourceError(f"No images in {self.path}")
            self._cap = None
            self.duration = len(self._images) / self.fps
        elif self.path.suffix.lower() in VIDEO_EXTS:
            self._images = None
            self._cap = cv2.VideoCapture(str(self.path))
            if not self._cap.isOpened():
                raise SourceError(
                    f"Can't open {self.path}. If it's an iPhone HEVC video, convert it: "
                    "ffmpeg -i in.mov -c:v libx264 out.mp4"
                )
            n = self._cap.get(cv2.CAP_PROP_FRAME_COUNT)
            self._native_fps = self._cap.get(cv2.CAP_PROP_FPS) or 30.0
            self.duration = n / self._native_fps if n > 0 else float("inf")
        else:
            raise SourceError(f"Not a video or image folder: {self.path}")

    def read(self) -> Frame | None:
        if self.finished:
            return None
        if self._started is None:
            self._started = time.monotonic()

        target_t = self._index / self.fps
        if self.realtime:
            elapsed = time.monotonic() - self._started
            if elapsed < target_t:
                time.sleep(target_t - elapsed)
            else:
                # Fell behind: jump to the newest due frame instead of queueing.
                self._index = int(elapsed * self.fps)
                target_t = self._index / self.fps

        image = self._image_at(target_t)
        if image is None:
            if self.loop:
                self._rewind()
                return self.read()
            self.finished = True
            return None

        if self.call_quality:
            image = simulate_call(image)
        frame = Frame(image, target_t, self._index)
        self._index += 1
        return frame

    def _image_at(self, t: float):
        if self._images is not None:
            i = round(t * self.fps)
            return cv2.imread(str(self._images[i])) if i < len(self._images) else None
        # Video: decode forward to the frame for time t (no seeking: HEVC seeks are slow).
        target = round(t * self._native_fps)
        while int(self._cap.get(cv2.CAP_PROP_POS_FRAMES)) < target:
            if not self._cap.grab():
                return None
        ok, image = self._cap.read()
        return image if ok else None

    def _rewind(self) -> None:
        self._index = 0
        self._started = time.monotonic()
        if self._cap is not None:
            self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        self.finished = True
