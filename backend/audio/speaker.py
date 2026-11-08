"""Queue phrases for macOS `say`, routed to the call's virtual mic (BlackHole).

Speaking runs on its own thread so the game loop never waits for audio.
"""

from __future__ import annotations

import logging
import queue
import subprocess
import threading
from collections.abc import Callable

from backend import config

log = logging.getLogger(__name__)

# Runs one `say` command; returns True on success. Swapped out in tests.
Runner = Callable[[list[str]], bool]


def run_say(cmd: list[str]) -> bool:
    return subprocess.run(cmd, capture_output=True).returncode == 0


class Speaker:
    def __init__(
        self,
        device: str | None = None,
        voice: str | None = None,
        rate: int | None = None,
        runner: Runner = run_say,
    ):
        self.device = config.AUDIO_DEVICE if device is None else device
        self.voice = config.VOICE if voice is None else voice
        self.rate = config.RATE if rate is None else rate
        self.runner = runner
        self.last_text: str | None = None
        self.device_ok: bool | None = None      # None until the first attempt
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="speaker", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        if self._thread is not None:
            self._queue.put(None)
            self._thread.join(timeout=10)
            self._thread = None

    def say(self, text: str, force: bool = False) -> bool:
        """Queue `text`. The same text twice in a row is dropped unless `force`."""
        if not text or (text == self.last_text and not force):
            return False
        self.last_text = text
        self._queue.put(text)
        self.start()
        return True

    def repeat(self) -> bool:
        return self.say(self.last_text, force=True) if self.last_text else False

    def wait(self) -> None:
        """Block until everything queued so far has been spoken."""
        self._queue.join()

    def set_device(self, device: str) -> None:
        self.device = device
        self.device_ok = None

    def _command(self, text: str, with_device: bool) -> list[str]:
        cmd = ["say", "-r", str(self.rate)]
        if self.voice:
            cmd += ["-v", self.voice]
        if with_device and self.device:
            cmd += ["-a", self.device]
        return cmd + [text]

    def _run(self) -> None:
        while True:
            text = self._queue.get()
            try:
                if text is None:
                    return
                self._speak(text)
            finally:
                self._queue.task_done()

    def _speak(self, text: str) -> None:
        if self.device and self.device_ok is not False:
            ok = self.runner(self._command(text, with_device=True))
            if ok:
                self.device_ok = True
                return
            log.warning("Audio device %r not available; using default output", self.device)
            self.device_ok = False
        self.runner(self._command(text, with_device=False))
