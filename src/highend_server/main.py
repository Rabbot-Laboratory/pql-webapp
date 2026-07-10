from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from highend_server.api.routes import router
from highend_server.api.websocket_manager import WebSocketManager
from highend_server.application.control_service import ControlService
from highend_server.application.experiment import ExperimentRecorder
from highend_server.application.stabilization import StabilizationController
from highend_server.config import get_settings
from highend_server.domain.models import TelemetryEvent
from highend_server.sensors.sensor_service import SensorService
from highend_server.transport.serial_gateway import build_gateway

VUE_WEB_DIST_DIR = Path(__file__).resolve().parents[2] / "web-vue" / "dist"
PQL_A00_DESCRIPTION_DIR = Path(__file__).resolve().parents[2] / "pql-a00_description"
PQL_A00_MESH_DIR = Path(__file__).resolve().parents[2] / "pql-a00_description" / "meshes"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await app.state.control_service.connect()
    await app.state.sensor_service.start()
    await app.state.stabilization_controller.start()
    try:
        yield
    finally:
        await app.state.experiment_recorder.shutdown()
        await app.state.stabilization_controller.stop()
        await app.state.sensor_service.stop()
        await app.state.control_service.shutdown()


def create_app() -> FastAPI:
    settings = get_settings()
    websocket_manager = WebSocketManager()
    gateway = build_gateway(settings)
    # Recorder is created before the services so its event tee can be wired into
    # each service's event_sink at construction time. It is late-bound to the
    # services (recorder.bind) once they exist.
    experiment_recorder = ExperimentRecorder(settings=settings)

    async def event_sink(event: TelemetryEvent) -> None:
        await websocket_manager.broadcast(event)
        experiment_recorder.observe_event(event)

    control_service = ControlService(
        settings=settings, gateway=gateway, event_sink=event_sink
    )
    sensor_service = SensorService(settings=settings, event_sink=event_sink)
    control_service.set_attitude_provider(sensor_service.latest_attitude)
    control_service.set_level_offsets_provider(sensor_service.level_offsets)
    stabilization_controller = StabilizationController(
        settings=settings,
        control_service=control_service,
        attitude_provider=sensor_service.latest_attitude,
        level_offsets_provider=sensor_service.level_offsets,
        event_sink=event_sink,
        calibration_lock=sensor_service.calibration_lock,
    )
    experiment_recorder.bind(
        control_service=control_service,
        stabilization_controller=stabilization_controller,
        sensor_service=sensor_service,
    )
    # Authoritative (in-lock) side of the "no calibration while stabilization
    # is engaged" invariant; the route-level 409 pre-check alone is racy.
    sensor_service.set_stabilization_guard(lambda: stabilization_controller.enabled)

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
    app.state.sensor_service = sensor_service
    app.state.stabilization_controller = stabilization_controller
    app.state.experiment_recorder = experiment_recorder

    app.include_router(router, prefix="/api")
    if PQL_A00_DESCRIPTION_DIR.exists():
        app.mount(
            "/robot-description/pql-a00",
            StaticFiles(directory=PQL_A00_DESCRIPTION_DIR),
            name="pql-a00-description",
        )
    if PQL_A00_MESH_DIR.exists():
        app.mount(
            "/robot-assets/pql-a00/meshes",
            StaticFiles(directory=PQL_A00_MESH_DIR),
            name="pql-a00-meshes",
        )
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
