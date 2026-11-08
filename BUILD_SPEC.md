# Glasses Chess Engine: Build Spec for Claude Code

Put this file in the repo root and start Claude Code with:
> "Read BUILD_SPEC.md. We'll build this phase by phase. Start with Phase 0."

Copy the **CLAUDE.md** section into its own `CLAUDE.md` file during Phase 0.

---

## 1. What we're building

An iOS app plus a Python backend. The app streams camera frames from Ray-Ban Meta glasses, the backend reads the physical chess board, and the glasses speak Stockfish's best move.

**User flow**
1. Open the app, pick a color (White/Black), tap **Start**.
2. Look at the board in its starting position for ~2 seconds. The backend locks the board corners and orientation. The app says "Board locked."
3. Whenever it's the user's turn, the app speaks the best move in plain English ("Knight to f3", "Castle kingside").
4. The user plays the move. The system confirms it and waits for the opponent's move.
5. If the board can't be read reliably, the app says "Lost track, look at the board" and resyncs.
6. Tap **End**. The game is saved as a PGN.

**Out of scope:** hints, coaching, LLM explanations, on-glasses display (Gen 3 has no display), online play.

**Latency target:** < 3 s from the opponent's hand leaving the board to speech starting.

---

## 2. Stack

| Layer | Choice |
|---|---|
| Glasses SDK | Meta Wearables Device Access Toolkit (iOS, Swift Package Manager) |
| iOS app | Swift / SwiftUI, AVSpeechSynthesizer for TTS (glasses act as the Bluetooth audio output) |
| Backend | Python 3.11, FastAPI, uvicorn |
| Vision | OpenCV (warp, corner detection), Ultralytics YOLO or a small CNN for square classification |
| Chess logic | python-chess |
| Engine | Stockfish (UCI via `chess.engine`) |
| Tests | pytest |
| Phone ↔ laptop | same Wi-Fi, laptop's LAN IP; fall back to ngrok / cloudflared tunnel |

**Before starting:**
- Install the Meta DAT Claude Code skill from the `facebook/meta-wearables-dat-ios` repo (see its README).
- Check the SDK's Version Dependencies page to confirm Ray-Ban Meta Gen 3 is supported on your firmware.
- Enable Developer Mode on the glasses.

---

## 3. Repo layout

```
glasses-chess/
├── CLAUDE.md
├── BUILD_SPEC.md
├── backend/
│   ├── app.py                  # FastAPI routes
│   ├── config.py               # thresholds, engine time, paths
│   ├── session.py              # per-game state object
│   ├── vision/
│   │   ├── board.py            # corner detection + homography warp
│   │   ├── calibrate.py        # lock corners + orientation at game start
│   │   └── squares.py          # 64-square classifier: empty / white / black
│   ├── game/
│   │   ├── tracker.py          # occupancy diff -> unique legal move
│   │   ├── engine.py           # Stockfish wrapper
│   │   └── speech.py           # chess.Move -> plain-English string
│   ├── debug/                  # saved frames + warped boards (gitignored)
│   └── tests/
│       ├── test_tracker.py
│       ├── test_speech.py
│       ├── test_vision.py
│       └── fixtures/           # real board photos + expected occupancy JSON
├── ios/GlassesChess/
│   ├── GlassesChessApp.swift
│   ├── GlassesSession.swift    # DAT registration, permissions, stream lifecycle
│   ├── FrameUploader.swift     # throttle + POST frames
│   ├── Speaker.swift           # TTS, dedupe repeated phrases
│   ├── BackendClient.swift     # API calls + models
│   └── ContentView.swift       # color picker, start/end, debug board view
├── tools/
│   ├── capture_fixtures.py     # save frames for labeling
│   └── label_tool.py           # quick CLI/GUI to mark occupancy per square
└── games/                      # saved PGNs
```

---

## 4. CLAUDE.md (copy into its own file)

```markdown
# Glasses Chess

## Rules
- Run `pytest backend/tests` after every backend change. Never call a task done with failing tests.
- Vision outputs OCCUPANCY ONLY (empty / white / black per square). Piece identity always comes from game state in python-chess.
- Never send a position to Stockfish unless it was reached by a legal move from the last confirmed position.
- A move is accepted only after the same occupancy grid is seen on 2 consecutive frames (config: STABLE_FRAMES).
- iOS: develop against MockDeviceKit first, then real glasses.
- Keep the backend stateless per request except for the Session object keyed by session_id.
- Save every received frame and warped board to backend/debug/ when DEBUG=1.

## Commands
- Backend: `cd backend && uvicorn app:app --host 0.0.0.0 --port 8000 --reload`
- Tests: `pytest backend/tests -q`
- iOS: open ios/GlassesChess.xcodeproj in Xcode
```

---

## 5. Module specs

### 5.1 `game/tracker.py`

```python
Occupancy = list[list[str]]  # 8x8, values "e" | "w" | "b", indexed [rank][file], rank 0 = rank 1

def occupancy_of(board: chess.Board) -> Occupancy: ...

def find_move(prev: chess.Board, observed: Occupancy) -> MoveResult: ...
# MoveResult: status in {"no_change", "move", "ambiguous", "illegal"}, move: chess.Move | None
```

Algorithm:
1. If `observed == occupancy_of(prev)`, return `no_change`.
2. For each legal move in `prev`: push it, compute the occupancy, pop. Collect the moves whose occupancy equals `observed`.
3. Exactly one match: `move`. More than one: `ambiguous` (only promotions should collide; default to queen promotion and return `move`). None: `illegal`.

This handles castling, en passant, and captures automatically, because each is just a different occupancy pattern.

Tests must cover: a normal move, a capture, kingside and queenside castling for both colors, en passant, promotion (with and without capture), no change, an impossible grid, and a full famous game (e.g. the Opera Game) replayed move by move.

### 5.2 `game/speech.py`

`move_to_speech(board, move) -> str` (call before pushing the move):
- Castling: "Castle kingside" / "Castle queenside"
- Pawn move: "Pawn to e4"; pawn capture: "Pawn takes d5"
- Piece move: "Knight to f3"; capture: "Bishop takes c6"
- Disambiguation when needed: "Rook from a1 to d1"
- Promotion: "Pawn to e8, promote to queen"
- Append ", check" or ", checkmate" when applicable

### 5.3 `game/engine.py`

- One persistent `chess.engine.SimpleEngine` per process, opened at startup and closed at shutdown.
- `best_move(board) -> chess.Move` using `chess.engine.Limit(time=ENGINE_TIME)` with ENGINE_TIME = 0.5 s by default.
- Path from env `STOCKFISH_PATH`, defaulting to `shutil.which("stockfish")`.

### 5.4 `vision/board.py` and `vision/calibrate.py`

- `find_corners(img) -> np.ndarray | None` returns 4 corners in pixel space. Try, in order:
  1. ArUco markers (4 small printed markers taped just outside the board corners; most reliable, recommended for the demo)
  2. Contour/Hough-line detection of the largest quadrilateral
- `warp(img, corners) -> np.ndarray` gives an 800×800 top-down board image.
- `calibrate(img, user_color) -> Calibration`:
  - Find and lock the corners.
  - Determine orientation: on the warped starting position, the two rows with light pieces are White's side. Rotate so rank 1 is White's back rank.
  - Store the corners, rotation, and per-square color statistics for the empty-square baseline.
- After calibration, re-detect corners only when the warp quality check fails (e.g. ArUco markers move more than 15 px, or the classification confidence average drops).

### 5.5 `vision/squares.py`

`classify(warped) -> (Occupancy, confidence: float)`

Start simple, then upgrade only if needed:
1. **v1, heuristic:** crop each square with a 10% inset and compare it against the calibrated empty-square baseline (edge density plus color variance) to decide occupied vs empty. Occupied squares are labeled white or black by mean brightness of the center region, thresholded per board from calibration.
2. **v2, learned:** fine-tune a tiny CNN (or YOLO classification) on 64×64 square crops from `tests/fixtures/` + `tools/label_tool.py` data. Three classes: e / w / b.

Return a confidence so `session.py` can reject bad frames (hand over board, motion blur).

### 5.6 `session.py`

```python
class Session:
    id: str
    user_color: chess.Color
    calibration: Calibration | None
    board: chess.Board
    pending: Occupancy | None      # candidate grid awaiting stability
    pending_count: int
    last_spoken: str | None
    lost_count: int
```

Per frame:
1. If not calibrated, attempt calibration and return `calibrating` or `locked`.
2. Warp and classify. If confidence < MIN_CONF, increment `lost_count`, return `unreadable`. When `lost_count >= LOST_LIMIT` (default 8), return `lost` once (spoken: "Lost track, look at the board").
3. Stability: if the grid equals `pending`, increment `pending_count`; otherwise set `pending` and reset the count. Act only when `pending_count >= STABLE_FRAMES`.
4. Run `tracker.find_move`. On `move`, push it onto the board.
5. If it is now the user's turn and the game isn't over, get the best move, build the speech string, and return it as `spoken_text`.
6. On game over, return "Checkmate" / "Stalemate" / "Draw" and save the PGN.

### 5.7 API (`app.py`)

```
POST /session                 body: {user_color: "white"|"black"}
                              -> {session_id}

POST /session/{id}/frame      body: multipart JPEG
                              -> {
                                   status: "calibrating"|"locked"|"waiting"|"unreadable"|
                                           "lost"|"your_move"|"game_over",
                                   fen: str | null,
                                   last_move: str | null,       # SAN
                                   best_move: str | null,       # SAN
                                   spoken_text: str | null,     # only when something new should be said
                                   occupancy: [[...]] | null    # for the debug view
                                 }

POST /session/{id}/reset      re-calibrate, keep game
POST /session/{id}/end        -> {pgn}
GET  /health
```

`spoken_text` is non-null only when there is something new to say, so the app can speak it unconditionally.

### 5.8 iOS app

- **GlassesSession.swift:** DAT registration flow (deeplink to the Meta AI app), camera permission, start/stop stream, pause handling (glasses removed or hinges closed). Use MockDeviceKit behind a debug toggle.
- **FrameUploader.swift:** take at most 1 frame per `FRAME_INTERVAL` (default 0.8 s), JPEG quality 0.6, resize the long edge to 1280. Drop frames while a request is in flight.
- **Speaker.swift:** AVSpeechSynthesizer with an AVAudioSession configured for Bluetooth output (category `.playback`, or `.playAndRecord` with `.allowBluetoothA2DP`), so audio goes to the glasses. Never speak the same string twice in a row.
- **ContentView.swift:** server URL field, color picker, Start/End/Recalibrate buttons, status label, and an 8×8 grid showing the latest `occupancy` plus `best_move` for debugging.

---

## 6. Build phases (one Claude Code session each)

Each phase ends with passing tests and a commit.

**Phase 0: scaffold**
> "Create the repo layout from BUILD_SPEC.md §3, write CLAUDE.md from §4, set up a venv with fastapi, uvicorn, python-chess, opencv-contrib-python, numpy, pytest, python-multipart. Verify `stockfish` is on PATH (brew install stockfish). Add a GET /health route and a test for it."

**Phase 1: chess brain (no vision)**
> "Implement game/tracker.py, game/speech.py, and game/engine.py per §5.1–5.3, with full pytest coverage including castling, en passant, promotion, and a full-game replay."

**Phase 2: session + API with fake vision**
> "Implement session.py and app.py per §5.6–5.7. For now, let /frame also accept a JSON occupancy grid (debug only) instead of a JPEG, so the whole loop can be tested without a camera. Write an integration test that plays a full game by posting occupancy grids and checks every spoken_text."

**Phase 3: fixtures**
> "Write tools/capture_fixtures.py (save frames from a webcam or a folder) and tools/label_tool.py (click squares to mark e/w/b, save as JSON next to each image)."
Then take ~50 photos of your real board from glasses height (starting position, mid-game positions, different lighting) and label them.

**Phase 4: vision**
> "Implement vision/board.py, calibrate.py, squares.py per §5.4–5.5 using the ArUco approach first. Add test_vision.py asserting ≥ 98% square accuracy on tests/fixtures. Save warped boards to backend/debug/ when DEBUG=1."

If heuristic accuracy is below 98%, move to v2:
> "Train the small e/w/b square classifier from the labeled fixtures and swap it into squares.py behind the same interface."

**Phase 5: iOS app with MockDeviceKit**
> "Using the Meta DAT skill, create the iOS app per §5.8. Start with MockDeviceKit feeding images from a bundled folder of fixtures, and verify the full loop against the local backend."

**Phase 6: real glasses**
> "Switch to the real device stream. Add the debug board view and tune FRAME_INTERVAL, STABLE_FRAMES, and MIN_CONF from real-world logs."

**Phase 7: polish**
> "Save PGNs to games/, add a /end summary, handle reconnects, and make the status messages clean."

---

## 7. Config defaults (`config.py`)

| Name | Default | Meaning |
|---|---|---|
| ENGINE_TIME | 0.5 | seconds per Stockfish search |
| STABLE_FRAMES | 2 | identical grids required before acting |
| MIN_CONF | 0.85 | below this a frame is "unreadable" |
| LOST_LIMIT | 8 | unreadable frames before saying "Lost track" |
| FRAME_INTERVAL | 0.8 | seconds between uploads (iOS) |
| DEBUG | 0 | save frames + warps |

---

## 8. Acceptance criteria

- [ ] All backend tests pass.
- [ ] Full game replay via the occupancy-grid debug endpoint produces correct speech for every user move.
- [ ] ≥ 98% square accuracy on fixtures, and 100% of fixture moves are correctly identified by the tracker.
- [ ] On real glasses: calibration succeeds within 3 s in normal indoor light.
- [ ] Median latency from a stable position to speech start < 3 s over a 20-move game.
- [ ] A hand over the board never causes a wrong move; at worst it delays one.
- [ ] Game saves as a valid PGN that imports into lichess.

---

## 9. Known risks and fallbacks

| Risk | Fallback |
|---|---|
| Corner detection unstable | ArUco markers taped at the board corners |
| Glare / outdoor light | Matte board, sit with the sun behind you, raise MIN_CONF |
| Head movement breaks the warp | Re-detect corners only via the markers; ask the user to "look at the board" |
| Gen 3 not supported by SDK version | Prototype with the phone camera through the same backend API |
| Bluetooth frame lag | Single-frame capture instead of the stream, sent on each glasses tap |
| Wrong move accepted | Recalibrate button + `/reset` that keeps the game and re-reads the board |
