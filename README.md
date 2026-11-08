# KnightSight

Real-time best chess move detection: KnightSight watches a physical chessboard through a camera, follows every move, and quietly says the best move on your turn.

© 2026 KnightSight. All rights reserved. This code is proprietary; see [LICENSE](LICENSE). Third-party components keep their own licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## What's here
- **Website** (`docs/`): the welcome page and the play page. Everything runs in the browser: board reading with OpenCV.js, move tracking with chess.js, and Stockfish 18 (WebAssembly). Works with any computer's webcam. Camera video never leaves the device.
- **iPhone app** (`ios/MetaChess`, shown as "KnightSight"): streams the Ray-Ban Meta glasses camera into the same play page, running on the phone, and speaks the best move into the glasses. No computer needed. Pocket mode blacks out the screen to save battery.
- **Python backend** (`backend/`): the original Mac version and the reference implementation. Used for development: tests, recordings and replays.

## Website
Run it locally:
```bash
.venv/bin/python -m http.server 8800 --bind 127.0.0.1
```
Then open http://localhost:8800/docs/. The play page is `docs/play.html`.

Published with GitHub Pages from the repository root (Settings → Pages → Deploy from a branch → `main`, `/ (root)`). The root `index.html` forwards to `docs/`, so the site is at `https://<user>.github.io/<repo>/docs/`.

Check the website's vision against a recording (frames plus New game / End presses), in a browser on the local server:
`http://localhost:8800/tools/web/replay.html?rec=recordings/20261108-131527`

Supported boards: green-and-white roll-up tournament boards. Wooden boards aren't supported yet.

## iPhone app
Full installation guide: https://stashm10.github.io/meta_chess/docs/glasses.html

Setup (once):
1. Install Xcode and sign in under Xcode → Settings → Apple Accounts.
2. In the Meta AI app: update the app and glasses firmware, and turn on Developer Mode (Settings → App Info → tap the version number 5 times).
3. Open `ios/MetaChess/MetaChess.xcodeproj`, select the MetaChess target → Signing & Capabilities → Team: your Personal Team.

Run: pick the iPhone at the top of Xcode and press ▶. With a free Apple ID the app stops opening after 7 days; press ▶ again to reinstall. The first time, trust the developer on the iPhone: Settings → General → VPN & Device Management.

In the app: **Connect glasses** (once), then **Start**, then **New game** and look at the starting position for about 3 seconds. The app loads the play page from the published website; change the address under ⓘ (for example to a Mac running the site locally).

## Python backend (development)
```bash
uv venv --python 3.12 .venv
uv pip install --python .venv -r requirements.txt
.venv/bin/pytest -q
.venv/bin/uvicorn backend.app:app --host 0.0.0.0 --port 8000
```
Stockfish for the backend: put a binary at `bin/stockfish` (gitignored) or on `PATH`, or set `STOCKFISH_PATH`.

Specs and design notes: `.plans/`.
