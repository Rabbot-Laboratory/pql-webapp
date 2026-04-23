from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from highend_server.api.dependencies import get_control_service
from highend_server.application.control_service import ControlService
from highend_server.domain.models import (
    CaptureRequest,
    ConnectionState,
    CsvPlaybackRequest,
    FixedMotionRequest,
    HealthResponse,
    ImportLegacyCsvRequest,
    LegId,
    MotionCategory,
    SaveMotionRequest,
    SetGainRequest,
    SetTargetRequest,
    StartTelemetryRecordingRequest,
    TelemetryRecordingStatus,
)


router = APIRouter()


def _validate_actuator_id(service: ControlService, actuator_id: int) -> None:
    actuator_count = len(service.list_actuators())
    if 0 <= actuator_id < actuator_count:
        return
    raise HTTPException(status_code=404, detail=f"Actuator {actuator_id} was not found")


@router.get("/health", response_model=HealthResponse)
async def health(service: ControlService = Depends(get_control_service)) -> HealthResponse:
    status = service.system_status
    return HealthResponse(
        ok=status.connection_state is ConnectionState.CONNECTED,
        service="highend-control-server",
        system=status,
    )


@router.get("/actuators")
async def list_actuators(service: ControlService = Depends(get_control_service)) -> dict:
    return {"items": [actuator.model_dump(mode="json") for actuator in service.list_actuators()]}


@router.get("/actuators/{actuator_id}")
async def get_actuator(actuator_id: int, service: ControlService = Depends(get_control_service)) -> dict:
    _validate_actuator_id(service, actuator_id)
    return {"item": service.get_actuator(actuator_id).model_dump(mode="json")}


@router.get("/preview/legs")
async def list_leg_previews(service: ControlService = Depends(get_control_service)) -> dict:
    return {"items": [preview.model_dump(mode="json") for preview in service.list_leg_previews()]}


@router.get("/preview/legs/{leg_id}")
async def get_leg_preview(leg_id: LegId, service: ControlService = Depends(get_control_service)) -> dict:
    return {"item": service.get_leg_preview(leg_id).model_dump(mode="json")}


@router.post("/actuators/{actuator_id}/target")
async def set_target(
    actuator_id: int,
    request: SetTargetRequest,
    service: ControlService = Depends(get_control_service),
) -> dict:
    _validate_actuator_id(service, actuator_id)
    item = await service.set_target(actuator_id, request)
    return {"item": item.model_dump(mode="json")}


@router.post("/actuators/{actuator_id}/gain")
async def set_gain(
    actuator_id: int,
    request: SetGainRequest,
    service: ControlService = Depends(get_control_service),
) -> dict:
    _validate_actuator_id(service, actuator_id)
    await service.set_gain(actuator_id, request)
    return {"ok": True}


@router.post("/actuators/{actuator_id}/gain/request")
async def request_gain(actuator_id: int, service: ControlService = Depends(get_control_service)) -> dict:
    _validate_actuator_id(service, actuator_id)
    await service.request_gain(actuator_id)
    return {"ok": True}


@router.post("/actuators/{actuator_id}/gain/save")
async def request_gain_save(actuator_id: int, service: ControlService = Depends(get_control_service)) -> dict:
    _validate_actuator_id(service, actuator_id)
    await service.request_gain_save(actuator_id)
    return {"ok": True}


@router.post("/actuators/{actuator_id}/capture")
async def request_capture(
    actuator_id: int,
    request: CaptureRequest,
    service: ControlService = Depends(get_control_service),
) -> dict:
    _validate_actuator_id(service, actuator_id)
    await service.request_capture(actuator_id, request)
    return {"ok": True}


@router.post("/motions/fixed")
async def fixed_motion(
    request: FixedMotionRequest,
    service: ControlService = Depends(get_control_service),
) -> dict:
    await service.start_fixed_motion(request)
    return {"ok": True}


@router.get("/motions/library")
async def list_motion_library(service: ControlService = Depends(get_control_service)) -> dict:
    snapshot = service.list_motion_library()
    return snapshot.model_dump(mode="json")


@router.get("/motions/library/{category}/{name}")
async def get_motion_file(
    category: MotionCategory,
    name: str,
    service: ControlService = Depends(get_control_service),
) -> dict:
    try:
        detail = service.get_motion_file(category, name)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return detail.model_dump(mode="json")


@router.post("/motions/library/{category}")
async def save_motion_file(
    category: MotionCategory,
    request: SaveMotionRequest,
    service: ControlService = Depends(get_control_service),
) -> dict:
    try:
        detail = service.save_motion_file(category, request)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    await service.publish_motion_library()
    return detail.model_dump(mode="json")


@router.delete("/motions/library/{category}/{name}")
async def delete_motion_file(
    category: MotionCategory,
    name: str,
    service: ControlService = Depends(get_control_service),
) -> dict:
    try:
        service.delete_motion_file(category, name)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    await service.publish_motion_library()
    return {"ok": True}


@router.post("/motions/import/legacy-csv")
async def import_legacy_csv(
    request: ImportLegacyCsvRequest,
    service: ControlService = Depends(get_control_service),
) -> dict:
    try:
        draft = service.import_legacy_csv(request)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return draft.model_dump(mode="json")


@router.post("/csv/playback/start")
async def start_csv_playback(
    request: CsvPlaybackRequest,
    service: ControlService = Depends(get_control_service),
) -> dict:
    await service.start_csv_playback(request)
    return {"ok": True}


@router.post("/csv/playback/stop")
async def stop_csv_playback(service: ControlService = Depends(get_control_service)) -> dict:
    await service.stop_csv_playback()
    return {"ok": True}


@router.get("/telemetry/recording", response_model=TelemetryRecordingStatus)
async def get_telemetry_recording_status(
    service: ControlService = Depends(get_control_service),
) -> TelemetryRecordingStatus:
    return service.telemetry_recording_status


@router.post("/telemetry/recording/start", response_model=TelemetryRecordingStatus)
async def start_telemetry_recording(
    request: StartTelemetryRecordingRequest | None = None,
    service: ControlService = Depends(get_control_service),
) -> TelemetryRecordingStatus:
    status = service.start_telemetry_recording(request)
    await service.publish_server_status()
    return status


@router.post("/telemetry/recording/stop", response_model=TelemetryRecordingStatus)
async def stop_telemetry_recording(
    service: ControlService = Depends(get_control_service),
) -> TelemetryRecordingStatus:
    status = service.stop_telemetry_recording()
    await service.publish_server_status()
    return status


@router.get("/telemetry/recording/latest")
async def download_latest_telemetry_log(
    service: ControlService = Depends(get_control_service),
) -> FileResponse:
    path = service.latest_telemetry_log_path()
    if path is None or not path.exists():
        raise HTTPException(status_code=404, detail="No telemetry log is available yet")
    return FileResponse(path=path, media_type="text/csv", filename=path.name)


@router.websocket("/ws")
async def websocket_stream(websocket: WebSocket) -> None:
    manager = websocket.app.state.websocket_manager
    service: ControlService = websocket.app.state.control_service
    await manager.connect(websocket)
    try:
        await websocket.send_json(
            {
                "type": "snapshot",
                "timestamp": service.system_status.updated_at.isoformat(),
                "payload": {
                    "system": service.system_status.model_dump(mode="json"),
                    "actuators": [item.model_dump(mode="json") for item in service.list_actuators()],
                    "legs": [item.model_dump(mode="json") for item in service.list_leg_previews()],
                },
            }
        )
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await manager.disconnect(websocket)
