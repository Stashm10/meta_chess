# Phase 3: Capture, Replay, and Recording: Spec

> **Update 2026-11-07:** the live source is now the iPhone app (`.plans/iphone-app-spec.md`), not the Messenger window. The window and region sources built here stay as fallbacks; file replay and recording are unchanged and still central.

Status: draft, from the follow-up interview on 2026-11-07. It extends `.plans/meta-chess-glasses-spec.md` (the main spec). Phases 0–2 are done: 61 tests pass.

## 1. What the photos showed

The user sent 4 iPhone photos (IMG_0244–0247) of the real board in the starting position.

| Finding | Effect on the design |
|---|---|
| Roll-up vinyl board: dark green and buff squares, a wide buff border with a–h and 1–8 printed on all 4 sides | The green-lattice board finder fits well. Green stands out clearly from the purple duvet and the beige wall. |
| Played on a bed. The vinyl is curled at the corners and wavy on the soft surface | **The board is not flat.** One global homography will misplace squares by up to half a square near the edges. The board finder must fit the grid locally (see 4). |
| Large Staunton pieces: cream white pieces, matte black pieces | Black on green or buff: easy. **Cream on buff squares: low contrast**, the hard case, as expected. |
| Pieces often sit off-center, for example a pawn on the front edge of f2 | The classifier must look at where the piece base sits, not only at the square center. |
| Head shadow over the d/e files (IMG_0246); warm indoor light | Calibration must not assume even lighting across the board. |
| The MacBook sits on the bed next to the board, in the frame | It is a large dark rectangle. The board finder must not mistake it for part of the board. |
| White's f1 knight and g1 bishop are swapped in every photo | Vision only sees occupancy, so this cannot be detected. **The user chose to ignore this.** It is a known risk, not handled. |

## 2. Decisions from this interview

| Topic | Decision |
|---|---|
| Wrong setup (swapped pieces) | Ignore. The user sets up correctly. No AI check, no reminder. |
| Playing surface | Both bed and table. **Build for the bent-board case.** |
| Photos | Copied into `backend/tests/fixtures/photos/` and committed. They count as starting-position fixtures and label themselves. |
| Live call check | Not done yet. Phase 3 is built against recorded footage, and the live call is checked afterwards. |
| Mac screen during a game | Call window and dashboard **side by side**. Capture a screen region that must stay visible. |
| Head position | Like IMG_0244: **far and low**, the hardest angle. Far ranks are squashed, and pieces on the back ranks hide the squares behind them. |
| If the live call is too blurry | Sit closer or lean in, like IMG_0245/0247. Keep the Messenger route. No native app. |
| Test footage | The user records **one short iPhone video** (see 6). |
| Opponent | A friend sits across. Their arms come in from the far side and cover the far ranks during their moves. |
| Leaked sound | Fine. The friend knows. No change. |

## 3. Phase 3 scope

### 3.1 `capture/source.py`: the shared interface
```python
class FrameSource(Protocol):
    def read(self) -> Frame | None   # None = no frame right now
    def close(self) -> None

@dataclass
class Frame:
    image: np.ndarray    # BGR
    t: float             # seconds (wall clock for live, media time for files)
    index: int
```

### 3.2 `capture/window.py`: the live call window
- `find_call_window()`: use `Quartz.CGWindowListCopyWindowInfo` to find on-screen windows owned by "Google Chrome" whose title contains "Messenger". Return the bounds of the largest one, in points.
- `RegionSource(region)`: grab that region with `mss` at `CAPTURE_FPS` (3). Convert BGRA to BGR. On Retina screens, mss returns physical pixels, so keep the full resolution.
- **Region choice:** the window is found automatically every few seconds (it follows moves and resizes). A region saved from the dashboard overrides that.
- The call window must stay visible, because the screen layout is side by side. If the window is not found, or the region is mostly one flat color (call ended, covered), report `capture: no_window`. The game stays silent.
- Without Screen Recording permission, macOS returns a black or empty grab. Detect it and show a clear message on the dashboard: "Grant Screen Recording to Terminal".
- **Keep awake:** while a game is running, run `caffeinate -d -i -w <server pid>` so the display does not sleep.

### 3.3 `capture/file_source.py`: replaying recorded footage
- `FileSource(path, fps=CAPTURE_FPS, loop=False, realtime=False)`:
  - A **video file** (.mov or .mp4) is read with `cv2.VideoCapture`, keeping one frame every `1/fps` seconds of media time.
  - A **folder of images** is read in sorted order.
  - `realtime=True` waits between frames so the dashboard plays at real speed. Tests run as fast as possible.
- iPhone videos are HEVC by default. If OpenCV cannot decode them, show the fix: iPhone Settings → Camera → Formats → **Most Compatible** (H.264), or convert with `ffmpeg -i in.mov -c:v libx264 out.mp4`.
- `.HEIC` stills are converted with `sips` (macOS built-in) by `tools/import_photos.py`, because OpenCV cannot read HEIC.

### 3.4 `capture/worker.py`: the frame loop
- A background thread: `source.read()` → `vision(frame)` → `session.observe(probs, conf)` or `session.observe_no_board()`.
- `vision` is a pluggable callable. In Phase 3 it is a **stub** that always returns "no board", so the game logic never sees real input until Phase 4. The Fake vision panel keeps working.
- If processing takes longer than the frame interval, frames are dropped. Frames are never queued.
- It keeps the latest raw frame, and later the warped board, for the dashboard.
- Start and stop come from the dashboard. Only one source is active at a time.

### 3.5 Recording (`RECORD=1` or the dashboard toggle)
- Path: `recordings/<session-timestamp>/frames/000123.jpg` (JPEG quality 90) plus `events.jsonl`.
- Each line of `events.jsonl` records one event: `{frame, t, kind: "frame"|"move"|"undo"|"new_game"|"lock"|"no_board", fen, uci?}`.
- **Auto-labels:** each saved frame is later assigned the FEN that was confirmed at that time. The frames *between* the first sign of change and the confirmation are marked `transition` and are not used for training or tests. This is the "labels itself" rule from the main spec.
- `tools/replay.py <recording>` turns a recording into a fixture set: stable frames with known occupancy.
- Recordings are gitignored. Small curated fixtures go in `backend/tests/fixtures/`.

### 3.6 Dashboard changes
- **Camera card:** a live preview of the latest frame from `GET /camera.jpg?v=<version>`. The image is not sent over the WebSocket, to keep messages small. It shows the capture status and the frame size, so the user sees early if the call is low resolution.
- **Source picker:** Messenger window (auto) / saved region / video file path / off. A "Pick region" button saves the current call-window bounds or a manually entered rectangle.
- **Record toggle** and the current recording folder.
- **Resolution check:** shows the frame size and, once Phase 4 finds the board, the average square height in pixels. Under ~20 px it warns: "Board too small, sit closer". This is the fallback the user chose.

### 3.7 API additions
```
GET  /camera.jpg                 latest frame (404 if none)
POST /capture/start   {source: "window"|"region"|"file", region?: [x,y,w,h], path?: str, realtime?: bool}
POST /capture/stop
GET  /capture/status  -> {source, running, fps_actual, frame_size, last_error}
POST /record          {on: bool} -> {path}
```

## 3.8 The glasses test video (received 2026-11-07)
`recordings/glasses-test/game1.mov` (gitignored, 576 MB): HEVC, **1552×2064 portrait**, 30 fps, 5:01. OpenCV reads it directly, so no conversion is needed.
- **Start position:** fully visible only in about the first 1.5 s, then the first move comes at about 3 s. That is enough for STABLE_FRAMES=3 at 3 fps, but just barely. The README gets a play tip: "hold your look on the starting position for 3 s".
- **Partial views are normal.** When the user leans in to move, the board shifts and the h-file (sometimes the g-file too) leaves the frame for a few seconds. Arms cover many squares during moves.
- **Board size in frame:** about 40% of the frame width at rest. At call resolution, about 720 px wide, that is roughly 35 px per square near the camera and about 15 px for the far ranks. This is the "sit closer" risk.
- The laptop is at the left edge, and the wall and door take up the top half.

## 4. Notes for Phase 4 (board finder on a bent board)
- **Squares outside the frame or not found are "unknown", not "empty":** vision gives them uniform probabilities, so they add nothing to any candidate's score. The tracker then judges moves from the visible squares only. A move on a hidden square is confirmed once that square is back in view. The `MAX_MISMATCH` check counts only visible squares.
- A frame is "good" only if enough squares are visible, for example ≥ 48 of 64 (`MIN_VISIBLE_SQUARES`). Otherwise it counts as no board.
Not built in Phase 3. Written down now while the photos are fresh.
- Find green square blobs, then the **corners of the 9×9 grid points**, not just the outer 4 corners. Fit each square's own quad from its 4 surrounding grid points. Where grid points are hidden by pieces, fill them in with a smooth (thin-plate or low-order polynomial) fit across the visible ones. A wavy board then still maps each square correctly.
- Use the buff border with printed labels only to find the board's outer edge. Do not OCR the labels; the starting position gives orientation.
- The far-low viewpoint means a piece's base sits in the **lower part** of its square in the image, and its body covers the square behind. The classifier samples a band at the near edge of each square's quad.
- The fixture photos are iPhone stills at about 4× the glasses' call resolution. Tests must **downscale them to ~720 px tall** before running the board finder.

## 5. Tests
- `FileSource`: a synthetic video (written with `cv2.VideoWriter` in a temp dir) gives the expected frame count at 3 fps. Image-folder order, `loop`, and end of stream are covered.
- `RegionSource`: `mss` and Quartz are mocked. The window is found by owner and title, the largest window wins, and black frames are reported as a permission problem.
- `CaptureWorker`: with a fake source and fake vision, frames reach `session.observe`, no-board frames reach `observe_no_board`, slow vision drops frames instead of queueing them, and stop is clean.
- Recording: frames and `events.jsonl` are written. Replay labels stable frames with the FEN of their time and marks transition frames.
- API: `/camera.jpg` returns 404 before the first frame and a JPEG after; start, stop and status work with a fake source.
- Fixtures: the 4 photos load, and each has a `.json` label with the starting occupancy.

## 6. User task: one test video (recorded on the Meta glasses)
Updated 2026-11-07: the user records with the glasses instead of an iPhone. That is more realistic: the real camera, the real head position, and the real head movement. The user plays both sides alone.
- **Recording:** use the glasses' own video recording (capture button), imported through the Meta AI app and then AirDropped to the Mac. About 3 minutes, which may need 2 clips if the glasses limit clip length.
- **Quality caveat:** a saved glasses video is sharper than the Messenger call stream. Tests also run it **downscaled to ~720p with heavy JPEG compression** to imitate the call.
- **Both sides by one person:** all hands come from the near side. Reach over to the far side for some Black moves, to imitate a friend's arms.
- **Format:** any. HEVC is converted if OpenCV cannot read it. The glasses may record in portrait; FileSource handles any aspect ratio.
- **Content, about 1–2 minutes:**
  1. About 3 s of the starting position.
  2. About 10 moves from both sides at normal speed, with your friend's arms reaching in. Include **one capture** and **castling**.
  3. About 3 s looking away from the board, then back.
- **Optional:** type the moves played (for example `e4 e5 Nf3 Nc6 …`) into a `moves.txt` file next to the video. It is used to check the tracker, not for labeling.
- Put it in `recordings/iphone-test/`, which is not committed.

## 7. Acceptance criteria
- [x] All tests pass (`.venv/bin/pytest -q`): 78 passed.
- [x] With the glasses video as a `FileSource` and `realtime=True`, the dashboard shows the footage at about 3 fps, and recording writes frames and events.
- [ ] (Needs Screen Recording permission and a Chrome window; not checked yet.) With a Messenger (or any Chrome) window open, the `window` source finds it automatically and previews it live, and it follows the window when moved or resized.
- [x] A missing Screen Recording permission shows a clear message. macOS returns the **wallpaper**, not a black image, so the source checks `CGPreflightScreenCaptureAccess` first.
- [x] The display does not sleep while capturing (`caffeinate -d -i`, stopped on stop or exit).
- [x] Phase 3 changes no game behavior: with the stub vision, a live capture never speaks.

## 8. After Phase 3
1. The user runs the live call check (README, "One-time call setup") and records one short real session with `RECORD=1`.
2. Phase 4: board finder (4), calibration, classifier. Tuned on the 4 photos, the iPhone video, and the real call recording.
