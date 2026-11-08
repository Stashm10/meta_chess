# iPhone App Route: Spec

Status: decided 2026-11-07. It **replaces the Messenger-call route** in `.plans/meta-chess-glasses-spec.md` (camera path, audio back, second account, BlackHole). The Messenger window capture from Phase 3 stays in the code as a fallback and for testing, but it is no longer the plan.

## 1. Why the switch
The user does not want a second Messenger account or phone number. Meta's **Wearables Device Access Toolkit (DAT)** lets an iPhone app get the glasses camera stream directly, and the phone plays audio to the glasses like any Bluetooth headset. The user also gets:
- no second account, no BlackHole, no "Hey Meta, call…", no camera switching each game;
- a sharper stream than a compressed video call (DAT caps the stream at **720p / 30 fps** over Bluetooth, and lowers it automatically when bandwidth drops);
- everything already built (tracker, Stockfish, speech phrasing, dashboard, file replay, recording) stays on the Mac.

## 2. Decisions
| Topic | Decision |
|---|---|
| Glasses | User says "Meta Gen 3". DAT's supported list (Nov 2026) names Ray-Ban Meta Gen 1/Gen 2, Optics, Meta Ray-Ban Display, Oakley Meta HSTN/Vanguard. "Gen 3" is not listed; it is probably Meta's "Gen 2" counted from Ray-Ban Stories. **Step 0 confirms it** (§6). |
| Signing | **Free Apple ID, $0.** The app expires after 7 days; reinstall from Xcode with one click. Free accounts also allow at most 3 sideloaded apps at a time. |
| Brain | **Stays on the Mac** (Python backend). The phone is camera in, voice out. Mac and iPhone on the same Wi-Fi. |
| Phone screen | **Minimal:** connection to glasses, connection to Mac, Start/Stop, one status line. Debugging stays on the Mac dashboard. |
| Voice | The phone speaks with `AVSpeechSynthesizer`, sent to the glasses over Bluetooth. Same phrases as now ("Knight to f3"). |
| Finding the Mac | Automatic over Bonjour (`_metachess._tcp`). A manual "Mac address" field is the fallback. |
| Messenger route | Kept as a fallback source on the dashboard. Not set up or tested further. |

## 3. Architecture
```
Ray-Ban Meta ──(DAT camera stream, Bluetooth)──▶ iPhone app "Meta Chess"
                                                  │ JPEG ~3 fps, HTTP over Wi-Fi
                                                  ▼
                                    Mac: Python backend (unchanged core)
                               PhoneSource → vision → tracker → Stockfish
                                                  │ phrases to say (in each response)
                                                  ▼
Ray-Ban Meta speakers ◀──(Bluetooth audio)── iPhone AVSpeechSynthesizer

Mac dashboard: http://localhost:8000 (debug, unchanged)
```

## 4. Backend changes (Mac, Python): buildable now, no Xcode needed
### 4.1 `capture/phone_source.py`
- `PhoneSource`: a single-slot "latest frame" buffer. Each upload **replaces** the slot; frames are never queued. Implements `FrameSource` (`paced=False`; the worker blocks on a condition until a new frame arrives or a timeout passes).
- `last_seen`: when the phone last uploaded. If it is more than 3 s old, the capture status shows "phone not sending".

### 4.2 Phone API
```
POST /phone/hello   {device: str, app_version: str} -> {session: str, fps: 3, max_side: 1280}
POST /phone/frame   body: image/jpeg, header X-Ack: <last speech id the phone spoke>
                    -> {status, speech: [{id, text}], game: {status, best_move, user_color}}
POST /phone/stop
GET  /phone/ping    -> {ok: true}   (reachability check from the app)
```
- `/phone/hello` makes the phone the active capture source automatically: the dashboard does not need to be touched.
- **Speech delivery:** the game session's speaker becomes `PhoneSpeaker`. Each phrase gets an increasing id and waits in a short queue. Each `/phone/frame` response carries every phrase newer than `X-Ack`. The phone speaks them in order and acknowledges, so nothing is lost or repeated when a request fails. Phrases older than 20 s are dropped, so stale moves are never spoken.
- `Speaker` (macOS `say`) stays available: the dashboard setting "Speak on: phone / Mac / both", default phone. The Mac voice is handy for testing with recorded video.

### 4.3 Bonjour advertising
- Advertise `_metachess._tcp` on port 8000 with the `zeroconf` Python package while the server runs.
- The server must listen on `0.0.0.0` (not `127.0.0.1`) so the phone can reach it. The first run makes macOS ask "Allow incoming connections": the user clicks Allow.

### 4.4 Tests
PhoneSource replaces instead of queueing, `/phone/frame` feeds the worker, speech ack and resend, dropping stale phrases, hello switches the source, ping, and invalid JPEG is rejected (422).

## 5. iPhone app (Swift/SwiftUI): needs Xcode 26.4+
Built with Claude Code using Meta's DAT plugin (`claude plugin marketplace add facebook/meta-wearables-dat-ios`, then `claude plugin install mwdat-ios@mwdat-ios-marketplace`). That plugin also adds Meta's docs server. It is installed with the user's OK when this phase starts.

- **Project:** `ios/MetaChess/`, iOS 17.2+, the DAT Swift package (1.0.x). The project is generated from a checked-in spec file, so it is reproducible.
- **GlassesLink:** handles DAT registration (a deep link to the Meta AI app), camera permission, start/stop stream, and pause when the glasses are taken off or folded. It asks for a low or medium stream; 720p max is more than enough.
- **Uploader:** takes the newest frame at most every 1/3 s, resizes the long side to 1280, encodes JPEG at quality 0.7, and POSTs it. If a request is still in flight, it skips that frame.
- **Voice:** `AVAudioSession` category `.playback`, routed to the glasses' Bluetooth output. It speaks the phrases from each response in id order, then acknowledges them.
- **Screen:** glasses state, Mac state (found automatically, or a typed address), a big Start/Stop button, and one status line ("Your move: Knight to f3"). The screen stays awake while streaming, because DAT streaming may need the app in the foreground. Step 1 checks this.
- **Info.plist:** local network usage text, Bonjour service `_metachess._tcp`, `NSAllowsLocalNetworking` (plain HTTP on the LAN), Bluetooth and DAT-required keys.
- **Mock Device Kit** first (frames from the glasses test video), then the real glasses.

## 6. Order of work
0. **User:** install **Xcode 26.4+** from the Mac App Store (free). In the Meta AI app: check the exact glasses name (Devices), update the app and the glasses firmware, and turn on **Developer Mode** (Settings → App Info → tap the version number 5 times → toggle on).
1. **Compatibility check:** the smallest DAT app that registers and shows "connected + receiving frames" on the real glasses. **If Gen 3 is not supported, stop here** and reconsider. The Messenger route is still built.
2. **Backend phone API** (§4). Can be done before or during step 0.
3. **Full iPhone app** (§5) against the backend, with Mock Device Kit and then the real glasses.
4. **Phase 4 vision** (board finder, classifier) continues as planned, unchanged: the glasses video is still the test material.

### Progress (2026-11-07)
- Step 0 done. The glasses are Ray-Ban Meta Wayfarers ("Wayfarer 03LS"), which are on DAT's supported list. The iPhone is a 16 Pro Max.
- Step 2 done: `capture/phone_source.py`, `audio/phone_speaker.py`, `bonjour.py`, phone routes in `app.py`, `backend/tests/test_phone.py`. A frame sent before hello returns 409, and the app then sends hello again.
- Step 1 app built at `ios/MetaChess`: DAT 1.0.1 exact, bundle id `com.romanstash.metachess`, no entitlements (the free team cannot use the Hotspot capability). It streams `.raw`, `.high`, 7 fps, and forwards 1 frame every 1/3 s. The Mac address is typed in for now; Bonjour browsing comes in step 3. **Step 1 passed on the real glasses (2026-11-07):** registration, camera permission, and streaming all work. The Mac received 720×1280 frames at about 2.7 fps, recovered from one 409 by sending hello again, and "Test voice" played in the glasses. The motion-blur risk at this frame rate still has to be checked on a real board.
- The dashboard has no "Speak on" control yet (`POST /settings {speak_on}` works).
- Step 4 (Phase 4 vision) done on 2026-11-08. The capture manager uses `BoardReader` by default, so phone frames are now read for real. - Step 3 done on 2026-11-08 (app v0.2):
  - `MacLink` browses `_metachess._tcp` with `NWBrowser` and resolves to an IPv4 `host:port`; a typed address is the fallback.
  - The screen has status chips (glasses, Mac), one headline from the Mac's game state, Start/Stop, and New game / Repeat / Undo. Diagnostics (preview, counts, address, voice test) are behind ⓘ.
  - The backend adds `board_visible` to the phone's game summary.
  - Built and installed. A live game on the real glasses has not been played yet.

## 7. Risks
| Risk | Fallback |
|---|---|
| Gen 3 not supported by DAT | Messenger route (already built), or wait for a DAT update |
| Free signing: 7-day expiry | Reinstall from Xcode before game night, about 1 minute |
| Streaming stops when the phone locks or the app goes to the background | Keep the app open with the screen on, as the plan already assumes |
| Glasses audio route switches during streaming (Bluetooth profile change) | Test early in step 1. If needed, use `.playAndRecord` with `.allowBluetoothA2DP` |
| Mac not found on Wi-Fi (guest network, client isolation) | Type the address manually, or use the Mac's hotspot or a phone hotspot |
| Bluetooth bandwidth drops the stream below 720p | Already expected: vision is built and tested at 720p call quality |

Sources: [Meta DAT iOS on GitHub](https://github.com/facebook/meta-wearables-dat-ios), [DAT getting started](https://wearables.developer.meta.com/wearables-docs/docs-markdown/develop/dat/getting-started-toolkit.md), [DAT FAQ](https://developers.meta.com/wearables/faq/), [UploadVR on stream limits](https://www.uploadvr.com/meta-wearables-device-access-toolkit-public-preview/).
