"""Phrases for the iPhone app to speak into the glasses.

Each phrase gets an increasing id. Every /phone/frame response carries the
phrases newer than the id the phone last acknowledged, so a failed request
never loses or repeats a phrase. Old phrases are dropped: a stale move must
never be spoken.
"""

from __future__ import annotations

import threading
import time

MAX_AGE = 20.0          # seconds a phrase may wait for the phone
SPEAK_ON = ("phone", "mac", "both")


class PhoneSpeaker:
    """Same interface as `Speaker`; optionally mirrors phrases to the Mac's own voice."""

    def __init__(self, mac=None, speak_on: str = "phone", clock=time.monotonic):
        self.mac = mac
        self.speak_on = speak_on
        self.clock = clock
        self.last_text: str | None = None
        self._next_id = 1
        self._pending: list[tuple[int, float, str]] = []
        self._lock = threading.Lock()

    def say(self, text: str, force: bool = False) -> bool:
        if not text or (text == self.last_text and not force):
            return False
        self.last_text = text
        if self.speak_on in ("phone", "both"):
            with self._lock:
                self._pending.append((self._next_id, self.clock(), text))
                self._next_id += 1
        if self.speak_on in ("mac", "both") and self.mac is not None:
            self.mac.say(text, force=True)
        return True

    def repeat(self) -> bool:
        return self.say(self.last_text, force=True) if self.last_text else False

    def pending(self, ack: int = 0) -> list[dict]:
        """Phrases the phone has not acknowledged yet, oldest first."""
        with self._lock:
            cutoff = self.clock() - MAX_AGE
            self._pending = [p for p in self._pending if p[0] > ack and p[1] >= cutoff]
            return [{"id": i, "text": t} for i, _, t in self._pending]

    def set_speak_on(self, where: str) -> None:
        if where not in SPEAK_ON:
            raise ValueError(f"speak_on must be one of {SPEAK_ON}")
        self.speak_on = where

    # Mac voice passthroughs, so the dashboard's audio settings keep working.
    @property
    def device(self) -> str | None:
        return getattr(self.mac, "device", None)

    def set_device(self, device: str) -> None:
        if hasattr(self.mac, "set_device"):
            self.mac.set_device(device)

    def stop(self) -> None:
        if hasattr(self.mac, "stop"):
            self.mac.stop()
