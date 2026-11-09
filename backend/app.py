"""FastAPI app: dashboard, WebSocket state stream, and game control routes."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

import chess
import numpy as np
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

from backend import config
from backend.audio.phone_speaker import SPEAK_ON, PhoneSpeaker
from backend.audio.speaker import Speaker
from backend.bonjour import Advertiser
from backend.capture.file_source import SourceError
from backend.capture.manager import CaptureManager
from backend.game.engine import Engine
from backend.local_only import LocalOnly
from backend.loop import GameSession

STATIC_DIR = Path(__file__).parent / "static"
GRID_CODES = {"e": 0, "w": 1, "b": 2}


def default_session() -> GameSession:
    return GameSession(Engine(), PhoneSpeaker(mac=Speaker(), speak_on=config.SPEAK_ON))


class NewGame(BaseModel):
    user_color: str | None = None      # "white" | "black"; None = auto-detect

    @field_validator("user_color")
    @classmethod
    def _color(cls, v):
        if v not in (None, "white", "black"):
            raise ValueError("user_color must be 'white' or 'black'")
        return v


class DebugObservation(BaseModel):
    """Fake vision. `grid` is 8 strings of e/w/b, rank 8 first (as on a diagram)."""
    grid: list[str] | None = None
    probs: list[list[float]] | None = None      # 64 x 3, a1 first
    conf: float = 0.95
    repeat: int = 1

    def to_probs(self) -> np.ndarray:
        if self.probs is not None:
            p = np.asarray(self.probs, dtype=np.float64)
            if p.shape != (64, 3):
                raise ValueError("probs must be 64 x 3")
            return p
        if self.grid is None or len(self.grid) != 8 or any(len(r) != 8 for r in self.grid):
            raise ValueError("grid must be 8 strings of 8 characters")
        p = np.full((64, 3), (1 - self.conf) / 2)
        for row, line in enumerate(self.grid):
            rank = 7 - row
            for file, ch in enumerate(line.lower()):
                if ch not in GRID_CODES:
                    raise ValueError(f"bad grid character {ch!r}")
                p[rank * 8 + file, GRID_CODES[ch]] = self.conf
        return p


class Settings(BaseModel):
    audio_device: str | None = None
    speak_on: str | None = None                  # "phone" | "mac" | "both"


class PhoneHello(BaseModel):
    device: str = "iPhone"
    app_version: str = ""
    new_game: bool = False


class CaptureStart(BaseModel):
    source: str                                  # "window" | "region" | "file"
    region: list[int] | None = None              # [x, y, w, h] in screen points
    path: str | None = None
    realtime: bool = True
    call_quality: bool = False                   # degrade file footage to Messenger-call quality
    loop: bool = False


class RecordToggle(BaseModel):
    on: bool


def create_app(
    session_factory: Callable[[], GameSession] = default_session,
    capture_factory: Callable[[GameSession], CaptureManager] = CaptureManager,
    bonjour: bool = False,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.session = session_factory()
        app.state.capture = capture_factory(app.state.session)
        advertiser = Advertiser(config.PORT) if bonjour else None
        if advertiser:
            await asyncio.to_thread(advertiser.start)
        yield
        if advertiser:
            await asyncio.to_thread(advertiser.stop)
        app.state.capture.shutdown()
        session = app.state.session
        if hasattr(session.engine, "close"):
            session.engine.close()
        if hasattr(session.speaker, "stop"):
            session.speaker.stop()

    app = FastAPI(title="Meta Chess", lifespan=lifespan)
    app.add_middleware(LocalOnly)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    def session(request: Request) -> GameSession:
        return request.app.state.session

    @app.get("/")
    def dashboard():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "stockfish": Path(config.STOCKFISH_PATH).is_file()}

    @app.get("/state")
    def get_state(request: Request) -> dict:
        return session(request).state()

    @app.post("/game/new")
    def new_game(request: Request, body: NewGame | None = None) -> dict:
        color = None
        if body and body.user_color:
            color = chess.WHITE if body.user_color == "white" else chess.BLACK
        session(request).new_game(color)
        return session(request).state()

    @app.post("/game/undo")
    def undo(request: Request) -> dict:
        return {"undone": session(request).undo(), **session(request).state()}

    @app.post("/game/recalibrate")
    def recalibrate(request: Request) -> dict:
        session(request).recalibrate()
        return session(request).state()

    @app.post("/game/end")
    def end(request: Request) -> dict:
        return {"pgn": session(request).end_game()}

    @app.post("/speak/repeat")
    def repeat(request: Request) -> dict:
        return {"spoken": session(request).repeat()}

    @app.post("/speak/test")
    def speak_test(request: Request) -> dict:
        s = session(request)
        return {"spoken": s.speaker.say("Knight to f3", force=True)}

    @app.post("/settings")
    def settings(request: Request, body: Settings) -> dict:
        speaker = session(request).speaker
        if body.audio_device is not None and hasattr(speaker, "set_device"):
            speaker.set_device(body.audio_device)
        if body.speak_on is not None:
            if body.speak_on not in SPEAK_ON or not hasattr(speaker, "set_speak_on"):
                raise HTTPException(422, f"speak_on must be one of {SPEAK_ON}")
            speaker.set_speak_on(body.speak_on)
        return get_settings(request)

    @app.get("/settings")
    def get_settings(request: Request) -> dict:
        speaker = session(request).speaker
        return {
            "audio_device": getattr(speaker, "device", None),
            "speak_on": getattr(speaker, "speak_on", "mac"),
            "stable_frames": config.STABLE_FRAMES,
        }

    def capture(request: Request) -> CaptureManager:
        return request.app.state.capture

    @app.post("/capture/start")
    def capture_start(request: Request, body: CaptureStart) -> dict:
        if body.source == "region" and (not body.region or len(body.region) != 4):
            raise HTTPException(422, "region must be [x, y, w, h]")
        if body.source == "file" and not body.path:
            raise HTTPException(422, "path is required for a file source")
        try:
            return capture(request).start(body.model_dump())
        except (SourceError, ValueError) as e:
            raise HTTPException(422, str(e)) from e

    @app.post("/capture/stop")
    def capture_stop(request: Request) -> dict:
        return capture(request).stop()

    @app.get("/capture/status")
    def capture_status(request: Request) -> dict:
        return capture(request).status()

    @app.get("/camera.jpg")
    def camera(request: Request):
        jpeg = capture(request).latest_jpeg()
        if jpeg is None:
            raise HTTPException(404, "no frame yet")
        return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.post("/record")
    def record(request: Request, body: RecordToggle) -> dict:
        return {"path": capture(request).set_recording(body.on)}

    # ---------- iPhone app ----------

    @app.get("/phone/ping")
    def phone_ping() -> dict:
        return {"ok": True}

    @app.post("/phone/hello")
    def phone_hello(request: Request, body: PhoneHello) -> dict:
        """The phone becomes the capture source; no need to touch the dashboard."""
        cap = capture(request)
        if cap.phone is None:
            cap.start({"source": "phone"})
        cap.phone.device = body.device
        if body.new_game:
            session(request).new_game()
        return {"fps": config.CAPTURE_FPS, "max_side": 1280, **phone_reply(request, ack=None)}

    @app.post("/phone/frame")
    async def phone_frame(request: Request) -> dict:
        phone = capture(request).phone
        if phone is None:
            raise HTTPException(409, "not the capture source: send /phone/hello first")
        try:
            ack = int(request.headers.get("x-ack", "0"))
        except ValueError:
            raise HTTPException(422, "X-Ack must be an integer") from None
        data = await request.body()
        try:
            await asyncio.to_thread(phone.push, data)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        return phone_reply(request, ack)

    @app.post("/phone/stop")
    def phone_stop(request: Request) -> dict:
        cap = capture(request)
        if cap.phone is not None:
            cap.stop()
        return {"stopped": True}

    def phone_reply(request: Request, ack: int | None) -> dict:
        """Game summary plus the phrases the phone still has to speak (none on hello)."""
        state = session(request).state()
        speaker = session(request).speaker
        # Advice only while it matters: mid-game, hands over the board hide squares all the time.
        hint = getattr(capture(request).vision, "hint", None) if state["status"] in ("calibrating", "check_board") else None
        speech = speaker.pending(ack) if ack is not None and hasattr(speaker, "pending") else []
        return {
            "status": state["status"],
            "speech": speech,
            "game": {**{k: state.get(k) for k in ("status", "best_move", "user_color", "spoken", "board_visible")}, "hint": hint},
        }

    @app.post("/debug/occupancy")
    def debug_occupancy(request: Request, body: DebugObservation) -> dict:
        try:
            probs = body.to_probs()
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        s = session(request)
        for _ in range(max(1, min(body.repeat, 20))):
            s.observe(probs)
        return s.state()

    @app.post("/debug/no_board")
    def debug_no_board(request: Request) -> dict:
        session(request).observe_no_board()
        return session(request).state()

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        s: GameSession = websocket.app.state.session
        sent = -1
        try:
            while True:
                if s.version != sent:
                    state = await asyncio.to_thread(s.state)
                    sent = state["version"]
                    await websocket.send_json(state)
                await asyncio.sleep(0.1)
        except WebSocketDisconnect:
            pass

    return app


app = create_app(bonjour=bool(config.BONJOUR))
