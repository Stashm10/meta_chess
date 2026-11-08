# Meta Chess

Real-time best chess move detection with gen 3 Meta glasses.

Ray-Ban Meta glasses stream their camera to a small iPhone app, which sends frames to this Mac. The Mac reads the physical board, runs Stockfish, and the phone speaks the best move into the glasses. Plan: [.plans/iphone-app-spec.md](.plans/iphone-app-spec.md). Full design: [.plans/meta-chess-glasses-spec.md](.plans/meta-chess-glasses-spec.md).

## Status
- Phases 0–3 are done: move tracker, speech, engine, game loop, speaker queue, live dashboard, and capture (Messenger window, screen region, or a recorded video) with recording and auto-labeling.
- Phone API is done: `/phone/hello`, `/phone/frame`, `/phone/stop`, `/phone/ping`, Bonjour `_metachess._tcp`, spoken phrases delivered to the phone.
- iPhone test app (`ios/MetaChess`): connects the glasses, streams the camera, sends frames to the Mac, and speaks replies. Verified on the real glasses: 720×1280 frames reach the Mac at about 3 fps, and speech plays in the glasses.
- Phase 4 vision is done: it finds the board, calibrates from the starting position (no labels), and reads each square as empty, white or black. On the glasses test video it locks as White in under 1 s and tracks the first 33 plies correctly. It then says "Check the board" where the recorded position stops being legal.
- iPhone app v0.3: it finds the Mac automatically (Bonjour), has one status line, Start/Stop, and New game / Repeat / Undo, with diagnostics behind the ⓘ button. While the board is not locked, it shows a short hint such as "Get the whole board in view" or "Sit closer and hold still".
- Live games played as White and as Black, tracked move for move (a 45-ply game as Black with castling on both sides and many captures). Calibration adapts to dim rooms.
- Dashboard: the Camera card draws the board grid vision found (your edge in orange) and says what vision sees. **Recalibrate** relearns the square colors from the current position mid-game (use it when the light changes); before the lock, it waits for the starting position again.

## Run
```bash
.venv/bin/uvicorn backend.app:app --host 0.0.0.0 --port 8000
```
Open http://localhost:8000. `0.0.0.0` lets the iPhone reach the Mac; allow incoming connections if macOS asks. Set `BONJOUR=0` to skip advertising, and `SPEAK_ON=mac|both` to hear moves from the Mac too (default: phone only).

- **Replay a video:** Camera card → "Video / image folder" → path such as `recordings/glasses-test/game1.mov` → Start capture. "Degrade to call quality" imitates Messenger's 720p stream.
- **Live call:** Camera card → "Messenger window (auto)". Keep the call window visible next to the dashboard. The display stays awake while capturing.
- **Record:** tick Record while capturing; frames and game events go to `recordings/<timestamp>/`. Label them with `.venv/bin/python tools/replay.py recordings/<timestamp>`.
- **Playing tip:** at the start of a game, look at the starting position for about 3 seconds before the first move so the board can lock.

- **Check vision on a video:** `.venv/bin/python tools/eval_video.py recordings/glasses-test/game1.mov --until 60` prints each confirmed move.

Without a camera, use the **Fake vision** panel: press **New game**, then **Send** with the starting position, then click squares to make moves and press **Send** again.

## Dev setup
```bash
uv venv --python 3.12 .venv
uv pip install --python .venv -r requirements.txt
.venv/bin/pytest -q
```

Stockfish: `bin/stockfish` is the official Stockfish 19 macOS universal build (GPLv3, license in `bin/STOCKFISH_LICENSE.txt`; `bin/` is gitignored). To use another binary, put it on `PATH` or set `STOCKFISH_PATH`.

## iPhone app
Setup (once):
1. Install **Xcode 26.4 or later** (free) and sign in under Xcode → Settings → Apple Accounts.
2. In the Meta AI app: update the app and glasses firmware, and turn on **Developer Mode** (Settings → App Info → tap the version number 5 times).
3. Keep the Mac and iPhone on the same Wi-Fi.
4. Open `ios/MetaChess/MetaChess.xcodeproj`, select the MetaChess target → Signing & Capabilities → Team: your Personal Team.

Run: pick the iPhone at the top of Xcode and press ▶. With a free Apple ID the app stops opening after 7 days; press ▶ again to reinstall. The first time, trust the developer on the iPhone: Settings → General → VPN & Device Management.

In the app:
1. The first time: **Connect glasses** (approve in Meta AI), and allow local network access.
2. **Start** turns on the glasses camera and starts sending frames to the Mac. The Mac is found by itself; type its address under ⓘ only if that fails.
3. **New game**, look at the starting position for about 3 seconds, then play. The best move is spoken in the glasses on your turn.
4. **Repeat** says the last phrase again. **Undo** takes back the last confirmed move. **Stop** ends streaming.

## Fallback: Messenger call setup (no longer the main route)
1. Install BlackHole 2ch (virtual audio device) and restart the Mac.
2. In Chrome, sign in to messenger.com with the second account. Set the site's microphone to **BlackHole 2ch**.
3. Grant Screen Recording permission to the app that runs the server (Terminal, or Claude if it starts it): System Settings → Privacy & Security → Screen & System Audio Recording. Restart the server afterwards. Without it, macOS only returns the wallpaper, and the dashboard says so.
4. Check, one at a time:
   - "Hey Meta, video call <name> on Messenger" rings on the Mac and you can answer it.
   - The call can be switched to the glasses camera, and the board is readable in the call window.
   - `say -a "BlackHole 2ch" "Knight to f3"` is heard in the glasses.
