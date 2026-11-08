"""Replay a recorded game video through vision and the move tracker, and print what was confirmed.

Usage: .venv/bin/python tools/eval_video.py recordings/glasses-test/game1.mov [--until SECONDS] [--fps 3]
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.capture.file_source import FileSource  # noqa: E402
from backend.loop import GameSession  # noqa: E402
from backend.vision.reader import BoardReader  # noqa: E402


class _Silent:
    last_text = None

    def say(self, text, force=False):
        self.last_text = text
        return True

    def repeat(self):
        return False


class _NoEngine:
    def best_move(self, board):
        return None


def replay(path: str, until: float = float("inf"), fps: float = 3.0, verbose: bool = False) -> dict:
    """Returns {moves: [SAN], statuses: [(t, status)], frames, no_board}."""
    source = FileSource(path, fps=fps, realtime=False)
    reader = BoardReader()
    session = GameSession(_NoEngine(), _Silent(), games_dir=Path(tempfile.mkdtemp(prefix="metachess-eval-")))
    session.listeners.append(reader.on_event)
    session.new_game()
    statuses, frames, no_board, last = [], 0, 0, None
    try:
        while True:
            frame = source.read()
            if frame is None:
                if source.finished:
                    break
                continue
            if frame.t > until:
                break
            frames += 1
            result = reader(frame)
            if result is None:
                no_board += 1
                session.observe_no_board()
            else:
                session.observe(result.probs, result.confidence, result.user_color)
            if session.status != last:
                last = session.status
                statuses.append((round(frame.t, 1), last))
                if verbose:
                    print(f"{frame.t:7.1f}s  {last:12s} {' '.join(session.state()['moves'][-2:])}")
    finally:
        source.close()
    return {"moves": session.state()["moves"], "statuses": statuses, "frames": frames, "no_board": no_board}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path")
    ap.add_argument("--until", type=float, default=float("inf"))
    ap.add_argument("--fps", type=float, default=3.0)
    args = ap.parse_args()
    t0 = time.time()
    out = replay(args.path, args.until, args.fps, verbose=True)
    print(f"\n{len(out['moves'])} plies: {' '.join(out['moves'])}")
    print(f"{out['frames']} frames, {out['no_board']} without a board, {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
