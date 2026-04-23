from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from highend_server.api.routes import router
from highend_server.api.websocket_manager import WebSocketManager
from highend_server.application.control_service import ControlService
from highend_server.config import get_settings
from highend_server.transport.serial_gateway import build_gateway

VUE_WEB_DIST_DIR = Path(__file__).resolve().parents[2] / "web-vue" / "dist"
PQL_A00_DESCRIPTION_DIR = Path(__file__).resolve().parents[2] / "pql-a00_description"
PQL_A00_MESH_DIR = Path(__file__).resolve().parents[2] / "pql-a00_description" / "meshes"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await app.state.control_service.connect()
    try:
        yield
    finally:
        await app.state.control_service.shutdown()


def create_app() -> FastAPI:
    settings = get_settings()
    websocket_manager = WebSocketManager()
    gateway = build_gateway(settings)
    control_service = ControlService(settings=settings, gateway=gateway, event_sink=websocket_manager.broadcast)

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=settings.allowed_origin_regex,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.settings = settings
    app.state.websocket_manager = websocket_manager
    app.state.control_service = control_service

    app.include_router(router, prefix="/api")
    if PQL_A00_DESCRIPTION_DIR.exists():
        app.mount("/robot-description/pql-a00", StaticFiles(directory=PQL_A00_DESCRIPTION_DIR), name="pql-a00-description")
    if PQL_A00_MESH_DIR.exists():
        app.mount("/robot-assets/pql-a00/meshes", StaticFiles(directory=PQL_A00_MESH_DIR), name="pql-a00-meshes")
    if VUE_WEB_DIST_DIR.exists():
        app.mount("/", StaticFiles(directory=VUE_WEB_DIST_DIR, html=True), name="web")
    return app


app = create_app()


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        "highend_server.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.api_reload,
    )
