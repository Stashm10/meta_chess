# Meta Chess

Ray-Ban Meta glasses stream their camera to a small iPhone app (Meta Wearables Device Access Toolkit); the app sends frames over Wi-Fi to the Python backend on the Mac, which reads the board, runs Stockfish, and returns the phrase the phone speaks into the glasses. Specs: `.plans/iphone-app-spec.md` (current route), `.plans/meta-chess-glasses-spec.md` (core design), `.plans/phase-3-capture-spec.md`. The older Messenger-call route remains only as a fallback source.

## Rules
- Run `.venv/bin/pytest -q` after every backend change. Never call a task done with failing tests.
- Vision outputs per-square probabilities of empty / white / black only. Piece identity always comes from the python-chess game state.
- Never send a position to Stockfish unless it was reached by legal moves from the last confirmed position.
- A move is accepted only when the same best candidate wins on STABLE_FRAMES consecutive good frames, by at least MIN_MARGIN.
- No markers, no manual photo labeling: calibration learns from the starting position; recordings are auto-labeled from confirmed game state.
- Games always start from the standard starting position.

## Commands
- Backend: `.venv/bin/uvicorn backend.app:app --host 0.0.0.0 --port 8000 --reload`
- Tests: `.venv/bin/pytest -q`
- Mac voice for testing: `say "Knight to f3"`
- The phone must reach the Mac: run the backend on `0.0.0.0`, same Wi-Fi.
