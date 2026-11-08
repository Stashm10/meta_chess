import json
import time

import cv2
import numpy as np
import pytest

from backend.capture.file_source import FileSource, SourceError
from backend.capture.labels import label_recording
from backend.capture.manager import CaptureManager
from backend.capture.recorder import Recorder
from backend.capture.source import Frame, simulate_call
from backend.capture.window import Region, RegionSource, find_call_window, looks_blank
from backend.capture.worker import CaptureWorker
from backend.vision.base import VisionResult


# ---------- helpers ----------

def write_video(path, seconds=3, fps=30, size=(64, 48)):
    """Each frame is a flat grey whose level encodes its frame number."""
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
    for i in range(seconds * fps):
        vw.write(np.full((size[1], size[0], 3), i * 2 % 256, np.uint8))
    vw.release()
    return path


def write_images(folder, n=5):
    folder.mkdir()
    for i in range(n):
        cv2.imwrite(str(folder / f"{i:03d}.png"), np.full((20, 30, 3), i * 40 % 256, np.uint8))
    return folder


class FakeSession:
    def __init__(self):
        self.observed = []
        self.no_board = 0
        self.fen = None
        self.listeners = []

    def observe(self, probs, confidence=1.0, user_color=None):
        self.observed.append((probs, confidence))

    def observe_no_board(self):
        self.no_board += 1

    def confirmed_fen(self):
        return self.fen


class ListSource:
    paced = False

    def __init__(self, n=5):
        self.n, self.i, self.finished, self.reads = n, 0, False, 0

    def read(self):
        self.reads += 1
        if self.i >= self.n:
            self.finished = True
            return None
        f = Frame(np.full((10, 10, 3), self.i, np.uint8), self.i / 3, self.i)
        self.i += 1
        return f

    def close(self):
        self.finished = True


def textured(h=40, w=60):
    return np.random.default_rng(0).integers(0, 255, (h, w, 4), dtype=np.uint8)


# ---------- FileSource ----------

def test_video_sampled_at_capture_fps(tmp_path):
    src = FileSource(write_video(tmp_path / "v.mp4"), fps=3)
    frames = []
    while (f := src.read()) is not None:
        frames.append(f)
    assert len(frames) == 9
    assert [round(f.t, 3) for f in frames[:3]] == [0, 0.333, 0.667]
    # Frame k of the sample is source frame 10k: grey level 20k (lossy codec, allow slack).
    assert abs(int(frames[2].image.mean()) - 40) <= 4
    assert src.finished


def test_image_folder_in_order_and_loop(tmp_path):
    folder = write_images(tmp_path / "imgs", 3)
    src = FileSource(folder, fps=3, loop=True)
    levels = [int(src.read().image.mean()) for _ in range(5)]
    assert levels == [0, 40, 80, 0, 40]


def test_realtime_skips_instead_of_queueing(tmp_path):
    src = FileSource(write_images(tmp_path / "imgs", 30), fps=10, realtime=True)
    src.read()
    time.sleep(0.35)                      # fall ~3 frames behind
    assert src.read().index >= 3


def test_bad_paths(tmp_path):
    with pytest.raises(SourceError):
        FileSource(tmp_path / "nope.txt")
    (tmp_path / "empty").mkdir()
    with pytest.raises(SourceError):
        FileSource(tmp_path / "empty")


def test_simulate_call_downscales():
    out = simulate_call(np.zeros((2064, 1552, 3), np.uint8))
    assert out.shape[0] == 720 and out.shape[1] == round(1552 * 720 / 2064)


# ---------- window finding ----------

def win(owner, title, w, h, layer=0, x=0, y=0):
    return {"owner": owner, "title": title, "layer": layer, "region": Region(x, y, w, h)}


def test_find_call_window_prefers_titled_messenger():
    wins = [win("Google Chrome", "Inbox", 1500, 900), win("Google Chrome", "Messenger | Call", 800, 600, x=5)]
    assert find_call_window(wins) == Region(5, 0, 800, 600)


def test_find_call_window_without_titles_uses_largest_browser():
    wins = [win("Google Chrome", "", 800, 600), win("Google Chrome", "", 1200, 800, x=9),
            win("Dock", "", 1500, 900, layer=20), win("Finder", "", 1500, 900)]
    assert find_call_window(wins) == Region(9, 0, 1200, 800)


def test_find_call_window_none_when_titles_visible_but_no_call():
    assert find_call_window([win("Google Chrome", "Inbox", 1000, 800)]) is None
    assert find_call_window([win("Google Chrome", "Messenger", 100, 100)]) is None


class FakeGrabber:
    def __init__(self, image):
        self.image, self.regions = image, []

    def grab(self, region):
        self.regions.append(region)
        return self.image


def test_region_source_grabs_bgr():
    g = FakeGrabber(textured())
    src = RegionSource(Region(1, 2, 60, 40), grabber=g, permission=lambda: True)
    f = src.read()
    assert f.image.shape == (40, 60, 3) and src.error is None
    assert g.regions[0] == {"left": 1, "top": 2, "width": 60, "height": 40}


def test_region_source_reports_blank_and_missing_window():
    src = RegionSource(Region(0, 0, 60, 40), grabber=FakeGrabber(np.zeros((40, 60, 4), np.uint8)), permission=lambda: True)
    assert src.read() is None and src.error
    assert looks_blank(np.zeros((40, 60, 3), np.uint8))
    missing = RegionSource(grabber=FakeGrabber(textured()), finder=lambda: None, permission=lambda: None)
    assert missing.read() is None and "not found" in missing.error
    denied = RegionSource(Region(0, 0, 60, 40), grabber=FakeGrabber(textured()), permission=lambda: False)
    assert denied.read() is None and "Screen Recording" in denied.error


# ---------- worker, recorder, labels ----------

def test_worker_routes_frames_to_session():
    session = FakeSession()
    probs = np.full((64, 3), 1 / 3)
    vision = lambda f: VisionResult(probs, 0.9) if f.index % 2 == 0 else None  # noqa: E731
    w = CaptureWorker(ListSource(5), session, vision, fps=100)
    w.start()
    w.wait(5)
    assert w.ended and w.frames == 5
    assert len(session.observed) == 3 and session.no_board == 2
    assert w.latest_jpeg()[:2] == b"\xff\xd8"


def test_worker_drops_frames_when_vision_is_slow():
    class Live(ListSource):
        paced = True
    session, src = FakeSession(), Live(1000)

    def slow(frame):
        time.sleep(0.1)
        return None
    w = CaptureWorker(src, session, slow, fps=50)
    w.start()
    time.sleep(0.55)
    w.stop()
    assert src.reads <= 8                  # ~5 processed, not 27 queued


def test_recorder_and_auto_labels(tmp_path):
    rec = Recorder(tmp_path, name="s1")
    fens = [None, "START"] + ["START"] * 15 + ["AFTER_E4"] * 5
    for i, fen in enumerate(fens):
        if i == 1:
            rec.event("lock", {"fen": "START"})
        if i == len(fens) - 5:
            rec.event("move", {"uci": ["e2e4"], "fen": "AFTER_E4"})
        rec.frame(Frame(np.zeros((8, 8, 3), np.uint8), i / 3, i), fen, True)
    rec.close()

    kinds = [json.loads(line)["kind"] for line in open(rec.dir / "events.jsonl")]
    assert kinds.count("frame") == len(fens) and "move" in kinds and "lock" in kinds
    assert len(list((rec.dir / "frames").glob("*.jpg"))) == len(fens)

    labels = label_recording(rec.dir, transition_seconds=2.0)
    move_frame = len(fens) - 5 - 1        # event logged after the previous frame
    trusted = [lf.frame for lf in labels if not lf.transition]
    assert 0 not in trusted               # before lock
    assert move_frame not in trusted and move_frame - 5 not in trusted   # within 2 s before the move
    assert move_frame - 7 in trusted and len(fens) - 1 in trusted


# ---------- manager ----------

def test_manager_plays_file_with_stub_vision_and_records(tmp_path):
    session = FakeSession()
    folder = write_images(tmp_path / "imgs", 4)
    m = CaptureManager(session, recordings_dir=tmp_path / "rec", keep_awake=False)
    path = m.set_recording(True)
    status = m.start({"source": "file", "path": str(folder), "realtime": False})
    assert status["source"] == "file"
    m.worker.wait(5)
    status = m.status()
    assert status["ended"] and status["frames"] == 4 and status["recorded_frames"] == 4
    assert session.no_board == 4 and not session.observed     # stub vision never sees a board
    assert m.latest_jpeg() is not None
    m.shutdown()
    assert m.status()["recording"] is None
    assert len(list((tmp_path / "rec").glob("*/frames/*.jpg"))) == 4 and path


def test_manager_rejects_unknown_source(tmp_path):
    m = CaptureManager(FakeSession(), keep_awake=False)
    with pytest.raises(ValueError):
        m.start({"source": "webcam"})
