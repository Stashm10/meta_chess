import threading

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend import config
from backend.app import create_app
from backend.audio.phone_speaker import MAX_AGE, PhoneSpeaker
from backend.capture.manager import CaptureManager
from backend.capture.phone_source import STALE_SECONDS, PhoneSource
from backend.capture.worker import CaptureWorker
from backend.loop import GameSession
from backend.tests.test_capture import FakeSession
from backend.tests.test_loop import FakeSpeaker, ScriptedEngine
from backend.vision.base import VisionResult


def jpeg(level=100, size=(40, 30)):
    ok, buf = cv2.imencode(".jpg", np.full((size[1], size[0], 3), level, np.uint8))
    return buf.tobytes()


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


# ---------- PhoneSource ----------

def test_phone_source_keeps_only_the_newest_frame():
    src = PhoneSource()
    src.push(jpeg(10))
    src.push(jpeg(200))
    frame = src.read()
    assert frame.index == 1
    assert abs(int(frame.image.mean()) - 200) < 3
    assert src.uploads == 2


def test_phone_source_read_waits_for_an_upload():
    src = PhoneSource()
    threading.Timer(0.05, src.push, args=(jpeg(),)).start()
    assert src.read() is not None


def test_phone_source_rejects_garbage():
    with pytest.raises(ValueError):
        PhoneSource().push(b"not a jpeg")
    with pytest.raises(ValueError):
        PhoneSource().push(b"")


def test_phone_source_reports_when_phone_stops_sending():
    clock = Clock()
    src = PhoneSource(clock=clock)
    assert "Waiting" in src.error
    src.push(jpeg())
    assert src.error is None
    clock.t += STALE_SECONDS + 1
    assert src.error == "Phone not sending frames"


def test_phone_frames_reach_the_session_through_the_worker():
    src = PhoneSource()
    session = FakeSession()
    worker = CaptureWorker(src, session, lambda f: VisionResult(np.full((64, 3), 1 / 3), 0.9))
    worker.start()
    try:
        src.push(jpeg())
        for _ in range(100):
            if session.observed:
                break
            threading.Event().wait(0.01)
    finally:
        worker.stop()
    assert len(session.observed) == 1


# ---------- PhoneSpeaker ----------

def test_phone_speaker_resends_until_acknowledged():
    sp = PhoneSpeaker()
    sp.say("Pawn to e4")
    sp.say("Knight to f3")
    assert [p["text"] for p in sp.pending(0)] == ["Pawn to e4", "Knight to f3"]
    first = sp.pending(0)[0]["id"]
    assert [p["text"] for p in sp.pending(0)] == ["Pawn to e4", "Knight to f3"]   # request lost: resend
    assert [p["text"] for p in sp.pending(first)] == ["Knight to f3"]
    assert sp.pending(first + 1) == []


def test_phone_speaker_drops_stale_phrases_and_duplicates():
    clock = Clock()
    sp = PhoneSpeaker(clock=clock)
    assert sp.say("Pawn to e4")
    assert not sp.say("Pawn to e4")
    clock.t += MAX_AGE + 1
    assert sp.pending(0) == []
    assert sp.repeat()
    assert [p["text"] for p in sp.pending(0)] == ["Pawn to e4"]


def test_phone_speaker_mirrors_to_mac():
    mac = FakeSpeaker()
    sp = PhoneSpeaker(mac=mac, speak_on="both")
    sp.say("Rook to d1")
    assert mac.spoken == ["Rook to d1"] and len(sp.pending()) == 1
    sp.set_speak_on("mac")
    sp.say("Queen to h5")
    assert mac.spoken[-1] == "Queen to h5" and len(sp.pending()) == 1
    with pytest.raises(ValueError):
        sp.set_speak_on("glasses")


# ---------- API ----------

START_GRID = ["bbbbbbbb", "bbbbbbbb", "eeeeeeee", "eeeeeeee", "eeeeeeee", "eeeeeeee", "wwwwwwww", "wwwwwwww"]


@pytest.fixture
def client(tmp_path):
    app = create_app(
        lambda: GameSession(ScriptedEngine(["e4", "e5"]), PhoneSpeaker(), games_dir=tmp_path),
        lambda s: CaptureManager(s, keep_awake=False, recordings_dir=tmp_path / "rec"),
    )
    with TestClient(app) as c:
        yield c


def test_ping(client):
    assert client.get("/phone/ping").json() == {"ok": True}


def test_frame_before_hello_is_refused(client):
    assert client.post("/phone/frame", content=jpeg()).status_code == 409


def test_hello_makes_phone_the_source_and_frames_flow(client):
    body = client.post("/phone/hello", json={"device": "test iPhone", "new_game": True}).json()
    assert body["fps"] == config.CAPTURE_FPS and body["status"] == "calibrating"
    assert body["game"]["hint"] == "Look at the starting position"
    status = client.get("/capture/status").json()
    assert status["source"] == "phone" and status["running"]

    r = client.post("/phone/frame", content=jpeg(), headers={"Content-Type": "image/jpeg", "X-Ack": "0"})
    assert r.status_code == 200
    assert r.json()["speech"] == []
    assert client.post("/phone/frame", content=b"junk").status_code == 422
    assert client.post("/phone/frame", content=jpeg(), headers={"X-Ack": "x"}).status_code == 422

    assert client.post("/phone/stop").json() == {"stopped": True}
    assert client.get("/capture/status").json()["running"] is False


def test_best_move_is_delivered_to_the_phone(client):
    client.post("/phone/hello", json={"new_game": True})
    client.post("/debug/occupancy", json={"grid": START_GRID, "repeat": config.STABLE_FRAMES})

    reply = client.post("/phone/frame", content=jpeg(), headers={"X-Ack": "0"}).json()
    assert reply["game"]["best_move"] == "e4"
    assert reply["game"]["user_color"] == "White" and "board_visible" in reply["game"]
    assert [p["text"] for p in reply["speech"]] == ["Board locked, you are White", "Pawn to e4"]
    spoken_id = reply["speech"][-1]["id"]
    again = client.post("/phone/frame", content=jpeg(), headers={"X-Ack": str(spoken_id)}).json()
    assert again["speech"] == []


def test_speak_on_setting(client):
    assert client.post("/settings", json={"speak_on": "both"}).json()["speak_on"] == "both"
    assert client.post("/settings", json={"speak_on": "glasses"}).status_code == 422
