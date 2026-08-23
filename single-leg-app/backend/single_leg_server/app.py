from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from single_leg_server.config import Settings, get_settings
from single_leg_server.controller import SingleLegController
from single_leg_server.models import CaptureRequest, GainRequest, TargetRequest, TelemetryEvent

APP_ROOT = Path(__file__).resolve().parents[2]
WEB_DIST = APP_ROOT / "web" / "dist"
ROBOT_DESCRIPTION = APP_ROOT.parent / "pql-a00_description"


class WebSocketHub:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()

    async def add(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._clients.add(websocket)

    def remove(self, websocket: WebSocket) -> None:
        self._clients.discard(websocket)

    async def broadcast(self, event: TelemetryEvent) -> None:
        payload = event.model_dump_json()
        stale: list[WebSocket] = []
        for client in tuple(self._clients):
            try:
                await client.send_text(payload)
            except Exception:
                stale.append(client)
        for client in stale:
            self.remove(client)


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime_settings = settings or get_settings()
    hub = WebSocketHub()
    controller = SingleLegController(runtime_settings, hub.broadcast)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await controller.start()
        try:
            yield
        finally:
            await controller.stop()

    app = FastAPI(title=runtime_settings.app_name, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"https?://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+)(:\d+)?",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.controller = controller

    @app.get("/api/health")
    async def health() -> dict:
        return {
            "ok": True,
            "service": runtime_settings.app_name,
            "system": controller.status.model_dump(mode="json"),
        }

    @app.get("/api/actuators")
    async def actuators() -> dict:
        return {"items": [item.model_dump(mode="json") for item in controller.list_actuators()]}

    @app.post("/api/actuators/{actuator_id}/target")
    async def set_target(actuator_id: int, request: TargetRequest) -> dict:
        try:
            item = await controller.set_target(actuator_id, request.mode, request.value)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ConnectionError as exc:
            raise HTTPException(status_code=409, detail="ESP32の接続を待っています") from exc
        return {"item": item.model_dump(mode="json")}

    @app.post("/api/actuators/{actuator_id}/gain")
    async def set_gain(actuator_id: int, request: GainRequest) -> dict:
        try:
            await controller.set_gain(actuator_id, request.p, request.i, request.d)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ConnectionError as exc:
            raise HTTPException(status_code=409, detail="ESP32の接続を待っています") from exc
        return {"ok": True}

    @app.post("/api/actuators/{actuator_id}/gain/request")
    async def request_gain(actuator_id: int) -> dict:
        try:
            await controller.request_gain(actuator_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ConnectionError as exc:
            raise HTTPException(status_code=409, detail="ESP32の接続を待っています") from exc
        return {"ok": True}

    @app.post("/api/actuators/{actuator_id}/gain/save")
    async def save_gain(actuator_id: int) -> dict:
        try:
            await controller.request_gain(actuator_id, save=True)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ConnectionError as exc:
            raise HTTPException(status_code=409, detail="ESP32の接続を待っています") from exc
        return {"ok": True}

    @app.post("/api/actuators/{actuator_id}/capture")
    async def capture(actuator_id: int, request: CaptureRequest) -> dict:
        try:
            await controller.capture(actuator_id, request.capture)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ConnectionError as exc:
            raise HTTPException(status_code=409, detail="ESP32の接続を待っています") from exc
        return {"ok": True}

    @app.websocket("/api/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await hub.add(websocket)
        await websocket.send_json(
            {
                "type": "snapshot",
                "payload": {
                    "system": controller.status.model_dump(mode="json"),
                    "actuators": [
                        item.model_dump(mode="json") for item in controller.list_actuators()
                    ],
                },
            }
        )
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            hub.remove(websocket)

    if ROBOT_DESCRIPTION.exists():
        app.mount(
            "/robot-description/pql-a00",
            StaticFiles(directory=ROBOT_DESCRIPTION),
            name="pql-a00-description",
        )
    if WEB_DIST.exists():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
    return app


app = create_app()


def run() -> None:
    settings = get_settings()
    uvicorn.run(create_app(settings), host=settings.api_host, port=settings.api_port)
