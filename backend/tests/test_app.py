import chess
import pytest
from fastapi.testclient import TestClient

from backend import config
from backend.app import create_app
from backend.loop import GameSession
from backend.tests.test_loop import FakeSpeaker, ScriptedEngine

START_GRID = ["bbbbbbbb", "bbbbbbbb", "eeeeeeee", "eeeeeeee", "eeeeeeee", "eeeeeeee", "wwwwwwww", "wwwwwwww"]


@pytest.fixture
def client(tmp_path):
    app = create_app(lambda: GameSession(ScriptedEngine(["e4", "e5"]), FakeSpeaker(), games_dir=tmp_path))
    with TestClient(app) as c:
        yield c


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert isinstance(body["stockfish"], bool)


def test_dashboard_served(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "<title>" in resp.text


def test_debug_grid_game_flow(client):
    assert client.post("/game/new", json={"user_color": "white"}).json()["status"] == "calibrating"
    state = client.post("/debug/occupancy", json={"grid": START_GRID, "repeat": config.STABLE_FRAMES}).json()
    assert state["status"] == "your_move"
    assert state["best_move"] == "e4"
    assert state["spoken"] == "Pawn to e4"

    after_e4 = START_GRID[:4] + ["eeeeweee", "eeeeeeee", "wwwwewww", "wwwwwwww"]
    state = client.post("/debug/occupancy", json={"grid": after_e4, "repeat": config.STABLE_FRAMES}).json()
    assert state["status"] == "waiting"
    assert state["moves"] == ["e4"]

    assert client.post("/game/undo").json()["undone"] == "e2e4"
    pgn = client.post("/game/end").json()["pgn"]
    assert pgn is None     # nothing played after undo
    assert client.get("/state").json()["status"] == "idle"


def test_debug_rejects_bad_grid(client):
    assert client.post("/debug/occupancy", json={"grid": ["xx"]}).status_code == 422
    assert client.post("/game/new", json={"user_color": "green"}).status_code == 422


def test_websocket_streams_state(client):
    with client.websocket_connect("/ws") as ws:
        first = ws.receive_json()
        assert first["status"] == "idle"
        client.post("/game/new")
        assert ws.receive_json()["status"] == "calibrating"


def noisy_probs(board: chess.Board, rng) -> list[list[float]]:
    from backend.tests.conftest import probs_for
    return probs_for(board, conf=0.85, noise=0.1, rng=rng).tolist()


def play_through_api(client, sans, rng) -> None:
    """Drive a whole game via /debug/occupancy with noisy fake vision."""
    client.post("/game/new", json={"user_color": "white"})
    client.post("/debug/occupancy", json={"probs": noisy_probs(chess.Board(), rng), "repeat": config.STABLE_FRAMES})
    ref = chess.Board()
    for san in sans:
        ref.push_san(san)
        for _ in range(20):
            state = client.post("/debug/occupancy", json={"probs": noisy_probs(ref, rng)}).json()
            if state["fen"] == ref.fen():
                break
        assert state["fen"] == ref.fen(), f"stuck after {san}"


def test_full_game_through_api(tmp_path, opera_game):
    import numpy as np
    from backend.game.speech import move_to_speech

    app = create_app(lambda: GameSession(ScriptedEngine(opera_game), FakeSpeaker(), games_dir=tmp_path))
    with TestClient(app) as client:
        play_through_api(client, opera_game, np.random.default_rng(11))
        assert client.get("/state").json()["status"] == "game_over"
        spoken = client.app.state.session.speaker.spoken

    ref, expected = chess.Board(), ["Board locked, you are White"]
    for san in opera_game:
        if ref.turn == chess.WHITE:
            expected.append(move_to_speech(ref, ref.parse_san(san)))
        ref.push_san(san)
    assert spoken == expected + ["Checkmate"]
    assert len(list(tmp_path.glob("*.pgn"))) == 1


@pytest.mark.skipif(not __import__("pathlib").Path(config.STOCKFISH_PATH).is_file(), reason="Stockfish not installed")
def test_real_stockfish_suggestions_are_legal(tmp_path):
    from backend.game.engine import Engine

    engine = Engine(think_time=0.05)
    app = create_app(lambda: GameSession(engine, FakeSpeaker(), games_dir=tmp_path))
    with TestClient(app) as client:
        client.post("/game/new", json={"user_color": "black"})
        client.post("/debug/occupancy", json={"grid": START_GRID, "repeat": config.STABLE_FRAMES})
        ref = chess.Board()
        ref.push_san("e4")
        from backend.tests.conftest import probs_for
        state = client.post("/debug/occupancy", json={"probs": probs_for(ref).tolist(), "repeat": config.STABLE_FRAMES}).json()
        assert state["status"] == "your_move"
        assert chess.Move.from_uci(state["best_move_uci"]) in ref.legal_moves
        assert state["spoken"]


def test_capture_routes(tmp_path):
    import cv2
    import numpy as np
    from backend.capture.manager import CaptureManager

    folder = tmp_path / "imgs"
    folder.mkdir()
    for i in range(3):
        cv2.imwrite(str(folder / f"{i}.png"), np.full((20, 30, 3), 50 * i, np.uint8))

    app = create_app(
        lambda: GameSession(ScriptedEngine(), FakeSpeaker(), games_dir=tmp_path),
        lambda s: CaptureManager(s, recordings_dir=tmp_path / "rec", keep_awake=False),
    )
    with TestClient(app) as client:
        assert client.get("/camera.jpg").status_code == 404
        assert client.post("/capture/start", json={"source": "file"}).status_code == 422
        assert client.post("/capture/start", json={"source": "region", "region": [1, 2]}).status_code == 422
        assert client.post("/capture/start", json={"source": "file", "path": str(tmp_path / "x.txt")}).status_code == 422

        rec = client.post("/record", json={"on": True}).json()["path"]
        assert rec.startswith(str(tmp_path / "rec"))
        status = client.post("/capture/start", json={"source": "file", "path": str(folder), "realtime": False}).json()
        assert status["source"] == "file"
        client.app.state.capture.worker.wait(5)
        status = client.get("/capture/status").json()
        assert status["ended"] and status["frames"] == 3 and status["frame_size"] == [30, 20]
        resp = client.get("/camera.jpg")
        assert resp.status_code == 200 and resp.headers["content-type"] == "image/jpeg"
        assert client.get("/state").json()["spoken"] is None      # stub vision: silent
        assert client.post("/capture/stop").json()["running"] is False
        assert client.post("/record", json={"on": False}).json()["path"] is None
