# Meta Chess Glasses: Spec

> **Update 2026-11-07:** the camera path changed from a Messenger video call to a small iPhone app using Meta's Device Access Toolkit. No second account and no BlackHole. See `.plans/iphone-app-spec.md`. Sections below about Messenger, BlackHole and the call window are superseded where they conflict.

Status: Phases 0–3 built. Phase 3 detail: `.plans/phase-3-capture-spec.md`. Originally drafted 2026-10-25. It replaces the parts of `BUILD_SPEC.md` that conflict with it (no iOS app, no ArUco markers, no photo labeling).

## 1. Summary

You wear Ray-Ban Meta glasses (camera and speakers, no display) during a casual game with friends who know about it. The glasses join a Messenger video call with a Mac on the same table or nearby. The Mac reads the physical board from the call video, follows the game, runs Stockfish, and speaks the best move into the call. You hear the move in the glasses.

**Is it possible?** Yes, with one important limit: a website cannot use the glasses camera. Meta exposes the camera only through its native SDK or through video calls. This spec uses the video-call route, so you do not need to build or install an app.

### Decisions from the interview

| Topic | Decision |
|---|---|
| Glasses | Ray-Ban Meta, no display. Output is audio only. |
| Board | Physical green-and-white tournament board, standard Staunton pieces |
| Use | Casual games with friends who know. Not for rated or tournament play. |
| Compute | Python backend on the Mac, same Wi-Fi |
| Camera path | Messenger video call from the glasses to a friend's or family member's account signed in on the Mac |
| Audio back | macOS TTS sent into a BlackHole virtual mic that the call uses as its microphone |
| Markers | None. The board is found from its green squares. |
| Photos / labeling | None. The system calibrates itself from the starting position. |
| Vision | Self-calibrating heuristic classifier that keeps learning from confirmed moves |
| Trigger | Automatic: speaks when it is your turn |
| Strength | Full Stockfish, one best move |
| Phrasing | Plain English: "Knight to f3", "Bishop takes c6", "Castle kingside" |
| Game start | Always from the standard starting position |
| Your color | Detected automatically: the color nearest the camera at the start is yours |
| Time control | Slow or no clock. A few seconds of lag is acceptable. |
| Bad board state | Say "Check the board" once and wait for a legal match |
| Looking away | Stay silent. Resume when the board is in view again. |
| Voice commands | Not in v1 |
| Mac UI | Live debug dashboard in the browser |
| Surface (2026-11-07) | Bed or table: the vinyl board may be curled and wavy, so the board finder fits the grid locally (see `.plans/phase-3-capture-spec.md` §4) |
| Head position (2026-11-07) | Far and low (like fixture IMG_0244). If the live call is too blurry, the user sits closer; no native app |
| Mac screen (2026-11-07) | Call window and dashboard side by side; capture a visible screen region |
| Setup errors (2026-11-07) | Swapped pieces can't be seen by occupancy-only vision; the user accepts this risk |

## 2. Architecture

```
Ray-Ban Meta ──(Messenger video call)──▶ messenger.com in Chrome on the Mac
                                               │ window capture (3 fps)
                                               ▼
                                  Python backend (FastAPI)
                         capture → board finder → square classifier
                                → move tracker (python-chess) → Stockfish
                                               │ "Knight to f3"
                                               ▼
                      `say -a "BlackHole 2ch"` → Chrome mic = BlackHole
                                               │
Ray-Ban Meta speakers ◀────(call audio)────────┘

Dashboard: http://localhost:8000 (WebSocket, live state)
```

### One-time setup (documented in README)
1. `brew install stockfish blackhole-2ch`, then restart the Mac so the audio driver loads.
2. In Chrome on the Mac, sign in to messenger.com with the second account. The Messenger desktop app is being retired, so use the web version. Check this in Phase 0.
3. In Chrome site settings for messenger.com, set the microphone to **BlackHole 2ch**.
4. Grant Screen Recording permission to the terminal or Python process.
5. To start a game: say "Hey Meta, video call <name> on Messenger", answer on the Mac, then switch the call to the glasses camera. Phase 0 confirms the exact switch gesture, which is usually a double press of the capture button.
6. Optional: create a Multi-Output Device in Audio MIDI Setup so you also hear the TTS on the Mac while debugging.

## 3. Repo layout

```
meta_chess/
├── CLAUDE.md
├── BUILD_SPEC.md                  # original notes (superseded where this spec differs)
├── .plans/meta-chess-glasses-spec.md
├── README.md                      # setup + how to play
├── pyproject.toml / requirements.txt
├── backend/
│   ├── app.py                     # FastAPI: dashboard, WebSocket, control routes
│   ├── config.py
│   ├── loop.py                    # capture → vision → tracker → engine → speech loop
│   ├── capture/
│   │   ├── window.py              # find Chrome/Messenger window bounds, grab frames (mss)
│   │   └── file_source.py         # replay a folder/video of frames (dev + tests)
│   ├── vision/
│   │   ├── board.py               # green-lattice detection → homography → 800×800 warp
│   │   ├── calibrate.py           # orientation, user color, per-square baselines
│   │   └── squares.py             # per-square P(empty/white/black), online learning
│   ├── game/
│   │   ├── tracker.py             # probabilistic match of observations to legal moves
│   │   ├── engine.py              # Stockfish wrapper
│   │   └── speech.py              # move → plain English
│   ├── audio/
│   │   └── speaker.py             # `say -a <device>`, dedupe, queue
│   ├── static/                    # dashboard HTML/JS/CSS
│   └── tests/
├── recordings/                    # auto-saved sessions (gitignored); become regression fixtures
└── games/                         # saved PGNs
```

## 4. Module specs

### 4.1 Capture (`capture/window.py`)
- Find the Chrome window showing the call with `Quartz.CGWindowListCopyWindowInfo` (owner "Google Chrome", title contains "Messenger"). The dashboard can override this with a saved screen region.
- Grab that region with `mss` at `CAPTURE_FPS` (default 3). Drop a frame if the previous one is still being processed.
- The call video is compressed and often 480p–720p. Make the call window large, ideally full screen on the Mac, to get the most pixels.
- `FileSource` uses the same interface and replays recorded frames for development and tests.

> **As built (2026-11-08), Phase 4.** Files: `vision/board.py`, `vision/squares.py`, and `vision/reader.py`. Calibration is part of the reader; there is no separate `calibrate.py`. What differs from 4.2–4.5 below:
> - **Board:** green blobs, then a lattice from one blob's corners, refit on all blobs. The board is turned so its near side is at the bottom ("view" cells u, v). The 8×8 window is the one that best explains which cells look green. A small quadratic correction fitted on blob corners handles the bend. Quality is the share of dark squares seen green times corner error relative to square size. `MIN_BOARD_QUALITY` = 0.5.
> - **Squares:** features from a base band (lower part), a body band (upper part), and the base band of the square in front. Model: logistic regression, refit after every confirmed position. Squares out of frame, or covered by strongly coloured objects, get uniform probabilities. Hands are not masked: pale skin looks like the cream pieces, so the tracker absorbs them. `MIN_VISIBLE_SQUARES` = 48.
> - **Calibration:** the first frame where the near and far two rows look occupied and the middle looks plain. The near rows' darkness decides the user's colour, which goes to `GameSession.observe(..., user_color)`. A dashboard override at lock relearns.
> - **Tracker, tuned on the glasses video:** `MAX_MISMATCH` = 1, because a square behind a tall piece is often misread. Frames where nothing fits neither count towards nor break a pending move's run of good frames. `BAD_LIMIT` = 12 (~4 s). After `BAD_LIMIT` bad frames, it also searches 3-ply candidates; transpositions are no longer counted as ambiguous.
> - **Not done yet:** a warped-board view and the "sit closer" warning on the dashboard; **Recalibrate** does not re-fit vision yet.

### 4.2 Board detection (`vision/board.py`), no markers
A green-and-white board is a strong signal:
1. Convert to HSV and threshold for green. Use auto-tuned hue bounds that calibration narrows.
2. Clean the mask with morphology and find connected components that are roughly square, the green squares.
3. Fit a lattice: estimate the two grid directions from neighbor vectors between centroids, assign each blob integer (i, j) lattice coordinates, and fit a homography with RANSAC. Many green squares stay visible when pieces cover some of them, so this tolerates occlusion and partial views.
4. Extend the lattice to the full 8×8 extent. Use the outer white border or board edge, and the known 32-dark-square parity, to pin the 8×8 window.
5. `warp(img, H)` produces an 800×800 top-down image (100 px per square).
6. Quality score: RANSAC inlier ratio, plus reprojection error, plus count of visible green squares. Below `MIN_BOARD_QUALITY`, the frame has "no board" and is ignored silently.

Head movement means the homography is recomputed **every frame**. It is cheap. Smooth it across frames only when the change is small.

### 4.3 Calibration (`vision/calibrate.py`)
Runs automatically when you press **New game** and the board is in the starting position.
- **Orientation:** the two rows nearest the camera hold your pieces. Rank 1 is the white side. a1 must be a dark (green) square, which settles left/right mirroring.
- **User color:** the color of the pieces in the rows nearest the camera. It is shown on the dashboard and can be overridden there.
- **Baselines with free labels:** the starting position labels all 64 squares (32 empty: 16 green, 16 white; 16 white pieces; 16 black pieces, on both square colors). Collect features from 3–5 stable frames and fit the classifier (4.4).
- Say "Board locked, you are White" (or Black), then start tracking.

### 4.4 Square classifier (`vision/squares.py`)
- Per square, use a crop with a 12% inset. Weight the **lower part** of the crop more heavily, because a piece's base sits there and its top leans into the square behind it in perspective.
- Features: mean and std in Lab, deviation from that square's empty baseline (green or white), edge density (Canny), and the dark-pixel and light-pixel fraction.
- Model: a small per-square-color logistic regression or k-NN (scikit-learn). Output P(empty), P(white), P(black) per square.
- **Online learning:** after each confirmed move, add the stable frame's features with labels taken from the known game state. Cap the buffer at the last N frames per class. The model adapts to lighting changes during the game.
- Frame confidence = mean of max class probabilities. If it is below `MIN_CONF`, for example because a hand is over the board, the frame is skipped.
- Known hard case: a white piece on a white square. It relies on edge, shadow, and texture features and on the tracker's legality prior (4.5).

### 4.5 Move tracker (`game/tracker.py`), probabilistic
Vision gives only occupancy. Piece identity always comes from the python-chess game state.
- Candidates: the current position (no change), every legal 1-ply move, and every legal 2-ply sequence. 2-ply covers you looking away while both sides moved, about 1,000 positions, which is cheap.
- Score each candidate with the log-likelihood Σ log P(observed class at square | candidate occupancy). Add a small prior penalty for 2-ply candidates.
- Accept the best candidate only if (a) it beats the runner-up by `MIN_MARGIN` and (b) the same best candidate wins on `STABLE_FRAMES` consecutive good frames (default 3, about 1 s).
- Promotions: assume queen.
- If no candidate fits (more than `MAX_MISMATCH` squares clearly disagree with the best candidate) on `BAD_LIMIT` consecutive good frames, say **"Check the board"** once. Keep the last confirmed position and keep matching. Recover silently when the board again matches the confirmed position or a legal continuation.
- Never send Stockfish a position that was not reached by legal moves from the last confirmed position.

### 4.6 Engine (`game/engine.py`)
- One persistent Stockfish process. `ENGINE_TIME` = 1.0 s (slow games). Full strength.
- When the opponent's move is confirmed, start the search immediately.

### 4.7 Speech (`game/speech.py`, `audio/speaker.py`)
- `move_to_speech(board, move)`: "Pawn to e4", "Pawn takes d5", "Knight to f3", "Bishop takes c6", "Castle kingside" or "Castle queenside", "Rook from a1 to d1" when disambiguation is needed, "Pawn to e8, promote to queen", with ", check" or ", checkmate" appended.
- Spoken events: "Board locked, you are White", the best move on your turn, "Check the board", and game end ("Checkmate", "Stalemate", "Draw").
- Silent: your own move confirmations, the opponent's move, and frames without a board.
- `speaker.py` runs `say -v <VOICE> -r <RATE> -a "<AUDIO_DEVICE>"` from a queue. It never says the same text twice in a row, except through the dashboard's **Repeat** button.

### 4.8 Dashboard (`static/`, served by `app.py`)
A single page at `http://localhost:8000`, updated live over a WebSocket:
- Live captured frame with the detected lattice overlaid
- Warped board with per-square predictions, colored by class and confidence
- The current game position (SVG board), move list, and last confirmed move
- The best move, and a status line: `no board` / `calibrating` / `tracking` / `your move` / `check board` / `game over`
- Buttons: **New game**, **Repeat**, **Undo last move**, **Recalibrate** (keeps the game), **End game** (saves the PGN to `games/`), **Test voice**
- Settings: capture region picker, audio device, record on/off

### 4.9 Control API
```
GET  /                      dashboard
WS   /ws                    state stream {status, fen, last_move, best_move, occupancy, probs, frame_jpeg?}
POST /game/new              start calibration
POST /game/undo
POST /game/recalibrate
POST /game/end    -> {pgn}
POST /speak/repeat
POST /speak/test
POST /debug/occupancy       body: 8x8 grid or probs (bypasses vision; tests + dev)
GET  /health
```

## 5. Testing without taking photos
- **Logic:** full pytest coverage for tracker, speech, and engine. Cover a normal move, a capture, castling on both sides for both colors, en passant, promotion with and without capture, no change, an impossible grid, a 2-ply catch-up, noisy probabilities, and a full famous game (the Opera Game) replayed.
- **Loop without vision:** an integration test plays a whole game through `/debug/occupancy` with noise added to the probabilities, and checks every spoken line.
- **Vision without manual photos:** when `RECORD=1`, every real session saves its frames, and each one is **auto-labeled from the confirmed game state**. These recordings become regression fixtures with no manual work. `FileSource` replays them in tests.
- **Bootstrap before the first real game:** develop board detection against a few public images or videos of green-and-white tournament boards, then switch to your own recordings.

## 6. Config defaults (`config.py`)
| Name | Default | Meaning |
|---|---|---|
| CAPTURE_FPS | 3 | frames grabbed from the call window per second |
| MIN_BOARD_QUALITY | 0.6 | below this, the frame has no board and is ignored |
| MIN_CONF | 0.80 | below this, the frame is skipped (hand, blur) |
| STABLE_FRAMES | 3 | consecutive frames with the same winning candidate before accepting |
| MIN_MARGIN | 2.0 | log-likelihood margin between the best and second-best candidate |
| MISMATCH_PROB / MAX_MISMATCH | 0.3 / 0 | squares that clearly disagree with the best candidate; more than MAX_MISMATCH = nothing fits |
| BAD_LIMIT | 6 | consecutive misfits before "Check the board" |
| ENGINE_TIME | 1.0 | seconds per Stockfish search |
| AUDIO_DEVICE | "BlackHole 2ch" | output for `say` |
| VOICE / RATE | system default / 190 | TTS voice and speed |
| RECORD | 0 | save frames + auto-labels to recordings/ |

## 7. Build phases
1. **Phase 0, scaffold and feasibility check:** create the repo layout, CLAUDE.md, a venv (fastapi, uvicorn, python-chess, opencv-python, numpy, scikit-learn, mss, pyobjc-framework-Quartz, pytest), and `/health`. Check by hand: the Messenger call from the glasses works on messenger.com, the glasses camera view shows in the call, `say -a "BlackHole 2ch"` is heard in the glasses, and window capture works.
2. **Phase 1, chess brain:** tracker (probabilistic, with 2-ply), speech, engine, and tests.
3. **Phase 2, loop and dashboard with fake vision:** `loop.py`, `/debug/occupancy`, WebSocket dashboard, speaker queue, and the full-game integration test.
4. **Phase 3, capture:** window finder, `mss` capture, `FileSource`, and recording.
5. **Phase 4, vision:** green-lattice board finder, calibration, self-learning classifier. Tune on real recordings from the glasses call.
6. **Phase 5, real games:** play 2–3 games with `RECORD=1`, tune thresholds, and turn the recordings into regression tests.
7. **Phase 6, polish:** README, PGN saving, call-drop handling (pause, then resume and re-match the last position).

## 8. Acceptance criteria
- [ ] All tests pass.
- [ ] A full game through `/debug/occupancy` with noisy probabilities produces the correct speech for every user turn.
- [ ] On a real call: the board locks within 5 s of pressing New game in normal indoor light.
- [ ] Median time from the opponent's move becoming stable to speech starting is under 4 s.
- [ ] A hand over the board never causes a wrong move; at worst it delays one.
- [ ] Looking away and back after the opponent moves still finds the move (1-ply or 2-ply).
- [ ] Recorded sessions replay with 100% of moves correctly identified.
- [ ] The saved PGN imports into lichess.

## 9. Risks and fallbacks
| Risk | Fallback |
|---|---|
| Messenger video is low resolution or heavily compressed | Full-screen call window; sit closer and look down; if still bad, use the native Meta SDK app from BUILD_SPEC.md |
| Messenger web call behaves differently than expected (mic choice, switching to glasses camera) | Try WhatsApp Desktop or a second WhatsApp number; last resort is the native SDK app |
| Call drops or glasses battery drains (video calls use a lot of power) | The loop pauses on "no board", then resumes and re-matches. Keep the glasses charging between games. |
| White piece on a white square is misread | Legality prior plus 2-ply matching plus online learning; add a "look closer" tip in README |
| Strong perspective hides squares behind tall pieces | Weight the base of each square; tell the user to look down at the board |
| Lighting changes during the game | Online re-learning from every confirmed move; Recalibrate keeps the game |
| Board not flat (bed, curled vinyl) | Local grid fit from 9×9 grid points instead of one global homography |
| Pieces set up wrong (e.g. knight and bishop swapped) | Not detectable by occupancy; accepted risk |
| Friend's Messenger account on the Mac | Use the dedicated Chrome profile; sign out after playing |

## 10. Out of scope (v1)
Voice commands, display output, top-N or Elo-limited suggestions, mid-game setup, clocks and blitz latency, online chess on a screen, a native iOS app.
