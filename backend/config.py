"""Tunable settings. Every value can be overridden with an environment variable of the same name."""

import os
import shutil
from pathlib import Path


def _env(name: str, default, cast=str):
    raw = os.environ.get(name)
    return default if raw is None else cast(raw)


ROOT = Path(__file__).resolve().parent.parent

# Capture
CAPTURE_FPS = _env("CAPTURE_FPS", 3.0, float)

# Vision
MIN_BOARD_QUALITY = _env("MIN_BOARD_QUALITY", 0.5, float)
MIN_CONF = _env("MIN_CONF", 0.80, float)
MIN_VISIBLE_SQUARES = _env("MIN_VISIBLE_SQUARES", 48, int)   # fewer squares in view = no board

# Tracker
STABLE_FRAMES = _env("STABLE_FRAMES", 3, int)
MIN_MARGIN = _env("MIN_MARGIN", 2.0, float)
MISMATCH_PROB = _env("MISMATCH_PROB", 0.3, float)  # a square "disagrees" if its expected class is below this
MAX_MISMATCH = _env("MAX_MISMATCH", 1, int)        # more disagreeing squares than this = nothing fits (1: one hidden square is normal)
BAD_LIMIT = _env("BAD_LIMIT", 12, int)            # ~4 s of frames nothing fits before "Check the board"
TWO_PLY_PENALTY = _env("TWO_PLY_PENALTY", 2.0, float)

# Engine
ENGINE_TIME = _env("ENGINE_TIME", 1.0, float)
STOCKFISH_PATH = _env(
    "STOCKFISH_PATH",
    shutil.which("stockfish") or str(ROOT / "bin" / "stockfish"),
)

# Audio
SPEAK_ON = _env("SPEAK_ON", "phone")                # phone | mac | both
AUDIO_DEVICE = _env("AUDIO_DEVICE", "")             # Mac voice output; "" = default speakers
VOICE = _env("VOICE", "")
RATE = _env("RATE", 190, int)

# Phone app
PORT = _env("PORT", 8000, int)                      # advertised over Bonjour; must match uvicorn --port
BONJOUR = _env("BONJOUR", 1, int)

# Debug
RECORD = _env("RECORD", 0, int)
RECORDINGS_DIR = ROOT / "recordings"
GAMES_DIR = ROOT / "games"
