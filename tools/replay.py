"""Auto-label a recording: python tools/replay.py recordings/<session> [--transition 4]

Writes <session>/labels.jsonl and prints a summary.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.capture.labels import TRANSITION_SECONDS, label_recording, write_labels  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("recording", type=Path)
    ap.add_argument("--transition", type=float, default=TRANSITION_SECONDS,
                    help="seconds before each confirmed move to treat as unreliable")
    args = ap.parse_args()

    labels = label_recording(args.recording, args.transition)
    out = write_labels(args.recording, labels)
    usable = [lf for lf in labels if not lf.transition]
    positions = {lf.fen for lf in usable}
    print(f"{len(labels)} frames, {len(usable)} usable, {len(positions)} distinct positions -> {out}")


if __name__ == "__main__":
    main()
