# Third-party notices

KnightSight's own code is proprietary (see `LICENSE`). These components are included unmodified and stay under their own licenses.

| Component | Version | Where | License | Source |
|---|---|---|---|---|
| Stockfish (via Stockfish.js) | 18 (npm `stockfish@18.0.8`, lite single-threaded WASM) | `docs/vendor/stockfish/` | GPL-3.0, text in `docs/vendor/stockfish/Copying.txt` | https://github.com/official-stockfish/Stockfish, https://github.com/nmrugg/stockfish.js |
| OpenCV.js | 5.0.0 (npm `@techstark/opencv-js@5.0.0-release.1`) | `docs/vendor/opencv/` | Apache-2.0, text in `docs/vendor/opencv/LICENSE.txt` | https://github.com/opencv/opencv |
| chess.js | 1.4.0 | `docs/vendor/chess/` | BSD-2-Clause, text in `docs/vendor/chess/LICENSE.txt` | https://github.com/jhlywa/chess.js |
| Meta Wearables Device Access Toolkit | 1.0.1 (Swift package, fetched by Xcode) | iPhone app | Meta's license terms | https://github.com/facebook/meta-wearables-dat-ios |

Stockfish runs as a separate program (a Web Worker in the browser) and KnightSight talks to it over the UCI protocol. Its complete source code is available at the links above.

The Python backend under `backend/` (a development and testing tool) uses python-chess (GPL-3.0), OpenCV (Apache-2.0), NumPy and scikit-learn (BSD-3-Clause), FastAPI and Uvicorn (MIT/BSD), installed separately from `requirements.txt` and not distributed in this repository.
