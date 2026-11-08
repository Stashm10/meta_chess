"""Live frames from the Messenger call window on this Mac's screen.

The call window must stay visible (the user keeps it side by side with the
dashboard): we grab a screen region, not the window's own buffer.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

from backend.capture.source import Frame

BROWSERS = ("Google Chrome", "Arc", "Safari", "Microsoft Edge", "Brave Browser", "Firefox")
CALL_TITLE = "Messenger"
REFIND_SECONDS = 2.0
MIN_WINDOW_SIDE = 200
PERMISSION_HELP = (
    "Screen Recording is off: allow it for the app running the server (Terminal or Claude) in "
    "System Settings → Privacy & Security → Screen & System Audio Recording, then restart the server"
)


@dataclass(frozen=True)
class Region:
    x: int
    y: int
    w: int
    h: int

    def as_mss(self) -> dict:
        return {"left": self.x, "top": self.y, "width": self.w, "height": self.h}


def screen_capture_allowed() -> bool | None:
    """True/False if macOS can tell us, None if unknown."""
    try:
        import Quartz
        return bool(Quartz.CGPreflightScreenCaptureAccess())
    except Exception:
        return None


def list_windows() -> list[dict]:
    import Quartz
    opts = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    out = []
    for w in Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID) or []:
        b = w.get("kCGWindowBounds") or {}
        out.append({
            "owner": w.get("kCGWindowOwnerName") or "",
            "title": w.get("kCGWindowName") or "",     # empty without Screen Recording permission
            "layer": int(w.get("kCGWindowLayer", 0)),
            "region": Region(int(b.get("X", 0)), int(b.get("Y", 0)), int(b.get("Width", 0)), int(b.get("Height", 0))),
        })
    return out


def find_call_window(windows: list[dict] | None = None, owners=BROWSERS, title=CALL_TITLE) -> Region | None:
    """Largest on-screen browser window titled like the call; any browser window if titles are hidden."""
    windows = list_windows() if windows is None else windows
    normal = [
        w for w in windows
        if w["layer"] == 0 and w["owner"] in owners
        and w["region"].w >= MIN_WINDOW_SIDE and w["region"].h >= MIN_WINDOW_SIDE
    ]
    titled = [w for w in normal if title.lower() in w["title"].lower()]
    if not titled and any(w["title"] for w in normal):
        return None          # titles are visible and none is the call
    pool = titled or normal
    if not pool:
        return None
    return max(pool, key=lambda w: w["region"].w * w["region"].h)["region"]


def looks_blank(image: np.ndarray) -> bool:
    """A grab without Screen Recording permission (or of a covered window) is one flat colour."""
    small = image[::8, ::8]
    return float(small.std()) < 2.0


class RegionSource:
    """Grabs a screen region. With `region=None` it finds the call window and follows it."""

    paced = True
    finished = False

    def __init__(self, region: Region | None = None, grabber=None, finder=find_call_window,
                 permission=screen_capture_allowed):
        self.fixed = region
        self.region = region
        self.finder = finder
        self.permission = permission
        self.error: str | None = None
        self._grabber = grabber
        self._last_find = 0.0
        self._index = 0

    def _grab(self, region: Region) -> np.ndarray:
        if self._grabber is None:
            import mss
            self._grabber = mss.MSS() if hasattr(mss, "MSS") else mss.mss()
        shot = self._grabber.grab(region.as_mss())
        return np.asarray(shot)[:, :, :3].copy()      # BGRA -> BGR

    def read(self) -> Frame | None:
        # Without permission macOS still returns an image: the desktop wallpaper, not the windows.
        if self.permission() is False:
            self.error = PERMISSION_HELP
            return None
        now = time.monotonic()
        if self.fixed is None and (self.region is None or now - self._last_find > REFIND_SECONDS):
            self._last_find = now
            self.region = self.finder()
        if self.region is None:
            self.error = "Call window not found: open messenger.com in Chrome and keep it visible"
            return None

        image = self._grab(self.region)
        if looks_blank(image):
            self.error = "Call window looks blank: is it covered, minimized, or the call ended?"
            return None

        self.error = None
        frame = Frame(image, time.time(), self._index)
        self._index += 1
        return frame

    def close(self) -> None:
        if self._grabber is not None and hasattr(self._grabber, "close"):
            self._grabber.close()
        self._grabber = None
