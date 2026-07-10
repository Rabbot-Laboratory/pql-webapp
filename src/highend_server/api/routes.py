from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from highend_server.api.dependencies import (
    get_control_service,
    get_experiment_recorder,
    get_sensor_service,
    get_stabilization_controller,
)
from highend_server.application.control_service import ControlService
from highend_server.application.experiment import (
    ExperimentAlreadyRunningError,
    ExperimentNotRunningError,
    ExperimentRecorder,
)
from highend_server.application.stabilization import StabilizationController
from highend_server.domain.models import (
    CaptureRequest,
    ConnectionState,
    CsvPlaybackRequest,
    ExperimentManifest,
    ExperimentNoteRequest,
    ExperimentStartRequest,
    ExperimentSummary,
    FixedMotionRequest,
    HealthResponse,
    ImportLegacyCsvRequest,
    ImuCalibrationRequest,
    LegId,
    MotionCategory,
    SaveMotionRequest,
    SetGainRequest,
    SetTargetRequest,
    StabilizationRequest,
    StabilizationState,
    StartTelemetryRecordingRequest,
    TelemetryRecordingStatus,
)
from highend_server.sensors.sensor_service import SensorService

router = APIRouter()


def _validate_actuator_id(service: ControlService, actuator_id: int) -> None:
    actuator_count = len(service.list_actuators())
    if 0 <= actuator_id < actuator_count:
        return
    raise HTTPException(status_code=404, detail=f"Actuator {actuator_id} was not found")


def _ensure_stabilization_idle(controller: StabilizationController) -> None:
    """Reject IMU calibration while stabilization is engaged.

    Calibration snaps the fusion filter to identity, which would cause a PID
    derivative kick on the real actuators. The user must disable stabilization
    first (this is the only new 409 relative to the Phase 2 contract).
    """
    if controller.enabled or controller.active:
        raise HTTPException(status_code=409, detail="stabilization active — disable first")


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


@router.get("/sensors")
async def get_sensors(service: SensorService = Depends(get_sensor_service)) -> dict:
    return {"item": service.state.model_dump(mode="json")}


@router.post("/sensors/imu/calibration/level")
async def calibrate_imu_level(
    service: SensorService = Depends(get_sensor_service),
    controller: StabilizationController = Depends(get_stabilization_controller),
    recorder: ExperimentRecorder = Depends(get_experiment_recorder),
) -> dict:
    _ensure_stabilization_idle(controller)
    try:
        state = await service.calibrate_level()
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    recorder.log_action("calibration", {"action": "level"})
    return {"item": state.model_dump(mode="json")}


@router.post("/sensors/imu/calibration/gyro-zero")
async def calibrate_imu_gyro_zero(
    request: ImuCalibrationRequest | None = None,
    service: SensorService = Depends(get_sensor_service),
    controller: StabilizationController = Depends(get_stabilization_controller),
    recorder: ExperimentRecorder = Depends(get_experiment_recorder),
) -> dict:
    _ensure_stabilization_idle(controller)
    try:
        state = await service.calibrate_gyro_zero((request or ImuCalibrationRequest()).sample_count)
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    recorder.log_action("calibration", {"action": "gyro_zero"})
    return {"item": state.model_dump(mode="json")}


@router.post("/sensors/imu/calibration/reset")
async def reset_imu_calibration(
    service: SensorService = Depends(get_sensor_service),
    controller: StabilizationController = Depends(get_stabilization_controller),
    recorder: ExperimentRecorder = Depends(get_experiment_recorder),
) -> dict:
    _ensure_stabilization_idle(controller)
    try:
        state = await service.reset_imu_calibration()
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    recorder.log_action("calibration", {"action": "reset"})
    return {"item": state.model_dump(mode="json")}


@router.post("/sensors/imu/calibration/mag/start")
async def start_mag_calibration(
    service: SensorService = Depends(get_sensor_service),
    controller: StabilizationController = Depends(get_stabilization_controller),
    recorder: ExperimentRecorder = Depends(get_experiment_recorder),
) -> dict:
    _ensure_stabilization_idle(controller)
    try:
        state = await service.start_mag_calibration()
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    recorder.log_action("calibration", {"action": "mag_start"})
    return {"item": state.model_dump(mode="json")}


@router.post("/sensors/imu/calibration/mag/finish")
async def finish_mag_calibration(
    service: SensorService = Depends(get_sensor_service),
    controller: StabilizationController = Depends(get_stabilization_controller),
    recorder: ExperimentRecorder = Depends(get_experiment_recorder),
) -> dict:
    _ensure_stabilization_idle(controller)
    try:
        state, quality = await service.finish_mag_calibration()
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    recorder.log_action("calibration", {"action": "mag_finish"})
    return {
        "item": state.model_dump(mode="json"),
        "quality": quality.model_dump(mode="json"),
    }


@router.post("/sensors/imu/calibration/mag/cancel")
async def cancel_mag_calibration(
    service: SensorService = Depends(get_sensor_service),
    recorder: ExperimentRecorder = Depends(get_experiment_recorder),
) -> dict:
    state = await service.cancel_mag_calibration()
    recorder.log_action("calibration", {"action": "mag_cancel"})
    return {"item": state.model_dump(mode="json")}


@router.get("/control/stabilization", response_model=StabilizationState)
async def get_stabilization(
    controller: StabilizationController = Depends(get_stabilization_controller),
) -> StabilizationState:
    return controller.get_state()


@router.post("/control/stabilization", response_model=StabilizationState)
async def set_stabilization(
    request: StabilizationRequest,
    controller: StabilizationController = Depends(get_stabilization_controller),
) -> StabilizationState:
    return await controller.apply_request(request)


@router.get("/actuators/{actuator_id}")
async def get_actuator(
    actuator_id: int, service: ControlService = Depends(get_control_service)
) -> dict:
    _validate_actuator_id(service, actuator_id)
    return {"item": service.get_actuator(actuator_id).model_dump(mode="json")}


@router.get("/preview/legs")
async def list_leg_previews(service: ControlService = Depends(get_control_service)) -> dict:
    return {"items": [preview.model_dump(mode="json") for preview in service.list_leg_previews()]}


@router.get("/preview/legs/{leg_id}")
async def get_leg_preview(
    leg_id: LegId, service: ControlService = Depends(get_control_service)
) -> dict:
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
async def request_gain(
    actuator_id: int, service: ControlService = Depends(get_control_service)
) -> dict:
    _validate_actuator_id(service, actuator_id)
    await service.request_gain(actuator_id)
    return {"ok": True}


@router.post("/actuators/{actuator_id}/gain/save")
async def request_gain_save(
    actuator_id: int, service: ControlService = Depends(get_control_service)
) -> dict:
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


@router.post("/experiments/start", response_model=ExperimentManifest)
async def start_experiment(
    request: ExperimentStartRequest,
    recorder: ExperimentRecorder = Depends(get_experiment_recorder),
) -> ExperimentManifest:
    try:
        return await recorder.start(request)
    except ExperimentAlreadyRunningError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/experiments/stop", response_model=ExperimentSummary)
async def stop_experiment(
    recorder: ExperimentRecorder = Depends(get_experiment_recorder),
) -> ExperimentSummary:
    try:
        return await recorder.stop()
    except ExperimentNotRunningError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/experiments/note")
async def add_experiment_note(
    request: ExperimentNoteRequest,
    recorder: ExperimentRecorder = Depends(get_experiment_recorder),
) -> dict:
    try:
        return await recorder.add_note(request.text)
    except ExperimentNotRunningError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/experiments")
async def list_experiments(
    recorder: ExperimentRecorder = Depends(get_experiment_recorder),
) -> dict:
    return {
        "experiments": [
            manifest.model_dump(mode="json") for manifest in recorder.list_experiments()
        ]
    }


@router.get("/experiments/latest", response_model=ExperimentManifest)
async def latest_experiment(
    recorder: ExperimentRecorder = Depends(get_experiment_recorder),
) -> ExperimentManifest:
    manifest = recorder.latest_experiment()
    if manifest is None:
        raise HTTPException(status_code=404, detail="no experiments recorded yet")
    return manifest


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
                    "actuators": [
                        item.model_dump(mode="json") for item in service.list_actuators()
                    ],
                    "legs": [
                        item.model_dump(mode="json") for item in service.list_leg_previews()
                    ],
                    "sensors": websocket.app.state.sensor_service.state.model_dump(mode="json"),
                    "stabilization": (
                        websocket.app.state.stabilization_controller.get_state().model_dump(
                            mode="json"
                        )
                    ),
                },
            }
        )
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await manager.disconnect(websocket)
