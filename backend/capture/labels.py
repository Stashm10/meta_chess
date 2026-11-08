"""Turn a recording into auto-labeled frames.

Each frame gets the FEN that was confirmed when it was captured. Frames shortly
before a move was confirmed (hand over the board, pieces in motion, stability
window) are marked as transitions and must not be used as ground truth.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

TRANSITION_SECONDS = 4.0


@dataclass
class LabeledFrame:
    frame: int
    path: str
    t: float
    fen: str | None          # confirmed position at capture time
    transition: bool         # True: don't trust `fen` for this frame
    board: bool              # vision saw a board


def read_events(recording: Path) -> list[dict]:
    with open(recording / "events.jsonl") as f:
        return [json.loads(line) for line in f if line.strip()]


def label_recording(recording: Path, transition_seconds: float = TRANSITION_SECONDS) -> list[LabeledFrame]:
    events = read_events(recording)
    frames = [e for e in events if e["kind"] == "frame"]
    t_by_frame = {e["frame"]: e["t"] for e in frames}
    # A move is confirmed while processing frame N; it changed sometime in the seconds before.
    change_times = [
        t_by_frame[e["frame"]] for e in events
        if e["kind"] in ("move", "undo", "lock") and e["frame"] in t_by_frame
    ]

    out = []
    for e in frames:
        t = e["t"]
        transition = e["fen"] is None or any(0 <= c - t < transition_seconds for c in change_times)
        out.append(LabeledFrame(
            frame=e["frame"],
            path=str(recording / "frames" / f"{e['frame']:06d}.jpg"),
            t=t,
            fen=e["fen"],
            transition=transition,
            board=e.get("board", False),
        ))
    return out


def write_labels(recording: Path, labels: list[LabeledFrame]) -> Path:
    out = recording / "labels.jsonl"
    with open(out, "w") as f:
        for lf in labels:
            f.write(json.dumps(asdict(lf)) + "\n")
    return out
