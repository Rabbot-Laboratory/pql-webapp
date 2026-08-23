from __future__ import annotations

import asyncio
import csv
import io
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from math import ceil
from pathlib import Path
from time import monotonic
from typing import TextIO
from zoneinfo import ZoneInfo

from highend_server.application.joint_preview import (
    build_leg_preview,
    build_leg_previews,
    leg_id_for_actuator,
)
from highend_server.config import Settings
from highend_server.domain.models import (
    POSITION_MAX,
    POSITION_MIN,
    ActuatorState,
    CaptureRequest,
    ConnectionState,
    ControlMode,
    CsvPlaybackRequest,
    DeviceEnvelope,
    FixedMotionRequest,
    ImportedMotionDraft,
    ImportLegacyCsvRequest,
    LegId,
    LegPreview,
    MotionCategory,
    MotionFileDetail,
    MotionLibraryItem,
    MotionLibrarySnapshot,
    PlaybackAdvanceMode,
    PlaybackStatus,
    PortRole,
    SaveMotionRequest,
    SetGainRequest,
    SetTargetRequest,
    StartTelemetryRecordingRequest,
    SystemStatus,
    TelemetryEvent,
    TelemetryRecordingScope,
    TelemetryRecordingStatus,
)
from highend_server.protocol.constants import NUM_ESP32_CONTROLLED_ACTUATORS
from highend_server.protocol.frames import (
    GainFrame,
    SensorFrame,
    build_request_capture_frame,
    build_request_gain_frame,
    build_request_gain_save_frame,
    build_set_gain_frame,
    build_set_target_frame,
    decode_frame,
    decode_transport_payload,
)
from highend_server.sensors.sensor_service import AttitudeState
from highend_server.transport.serial_gateway import SerialGateway

EventSink = Callable[[TelemetryEvent], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ControlSampleRow:
    """One actuator's control-plane snapshot for experiment logging.

    ``effective_target`` is produced by the SAME per-actuator computation the
    frame-send path uses (``ControlService._effective_target_for_index``), so
    the invariant ``effective_target == clamp(base_target + round(correction))``
    holds by construction.
    """

    actuator_id: int
    label: str
    control_mode: str
    actual_position: int
    base_target: int
    effective_target: int
    correction: float
    pressure: int


@dataclass(frozen=True, slots=True)
class ControlSample:
    """Immutable control-plane snapshot for all actuators at one instant."""

    rows: tuple[ControlSampleRow, ...]
    playback_row_index: int | None
    motion_name: str | None


# Phase 3: optional attitude access for the CSV playback row-advance guard.
# Mirrors the provider shape `StabilizationController` already uses.
AttitudeProvider = Callable[[], AttitudeState | None]
LevelOffsetsProvider = Callable[[], tuple[float, float]]
logger = logging.getLogger(__name__)
JST = ZoneInfo("Asia/Tokyo")


def build_rate_limited_position_rows(
    start: list[int],
    target: list[int],
    *,
    max_rate: float,
    interval_sec: float,
) -> list[list[str]]:
    """Interpolate a bounded-rate position move, excluding the start row."""
    if not start or len(start) != len(target):
        raise ValueError("home start and target vectors must have equal non-zero length")
    if max_rate <= 0.0 or interval_sec <= 0.0:
        raise ValueError("home rate and interval must be positive")
    max_difference = max(abs(end - begin) for begin, end in zip(start, target, strict=True))
    steps = max(1, ceil(max_difference / (max_rate * interval_sec)))
    return [
        [
            str(round(begin + (end - begin) * step / steps))
            for begin, end in zip(start, target, strict=True)
        ]
        for step in range(1, steps + 1)
    ]


class ControlService:
    def __init__(
        self,
        settings: Settings,
        gateway: SerialGateway,
        event_sink: EventSink,
        *,
        attitude_provider: AttitudeProvider | None = None,
        level_offsets_provider: LevelOffsetsProvider | None = None,
        time_fn: Callable[[], float] = monotonic,
    ) -> None:
        self.settings = settings
        self.gateway = gateway
        self.event_sink = event_sink
        self.gateway.set_device_callback(self.handle_device_payload)

        # Phase 3: optional attitude source for the CSV playback row-advance
        # guard (`_attitude_guard_state`). `main.py` constructs `ControlService`
        # before `SensorService` exists, so these are also settable later via
        # `set_attitude_provider`/`set_level_offsets_provider`. Left unwired,
        # the guard silently no-ops regardless of `playback_attitude_guard_deg`.
        self._attitude_provider = attitude_provider
        self._level_offsets_provider = level_offsets_provider or (lambda: (0.0, 0.0))
        self._time_fn = time_fn

        self._lock = asyncio.Lock()
        self._csv_task: asyncio.Task[None] | None = None
        # Exclusive owner flag for the lease-controlled adaptive walking loop.
        # It prevents manual targets and CSV playback from racing 25 Hz walking
        # target updates on the same pneumatic actuators.
        # Exclusive position-target owner: "adaptive walking" / "standing" / None.
        self._motion_owner: str | None = None
        self._playback_status = PlaybackStatus.IDLE
        self._current_motion_name: str | None = None
        self._current_motion_category: MotionCategory | None = None
        self._current_motion_loop = False
        # Index of the CSV playback row currently being driven (experiment log
        # `motion_frame`). None whenever playback is idle.
        self._playback_row_index: int | None = None
        self._actuators = self._build_initial_actuators(settings.actuator_count)
        self._telemetry_received_actuators: set[int] = set()
        self._actuator_telemetry_received_at: list[float | None] = [
            None
        ] * settings.actuator_count
        # Phase 2 stabilization composition layer. Corrections are position-target
        # offsets added on top of the user/CSV *base* targets at frame-send time.
        # Default all-zero => effective == base => byte-identical to prior behaviour.
        self._corrections: list[float] = [0.0] * settings.actuator_count
        # Last control mode driven per port. Corrections only re-send a port that
        # is in POSITION mode (never clobber a manually command-mode-driven port).
        self._last_mode: dict[PortRole, ControlMode] = {
            PortRole.FRONT: ControlMode.POSITION,
            PortRole.BACK: ControlMode.POSITION,
        }
        # Effective position fields last sent per port, for the correction deadband.
        # Seeded to the initial base so a zero-correction tick is a no-op.
        self._last_sent_position: dict[PortRole, list[int]] = {
            PortRole.FRONT: self._base_position_fields(PortRole.FRONT),
            PortRole.BACK: self._base_position_fields(PortRole.BACK),
        }
        self._telemetry_log_handle: TextIO | None = None
        self._telemetry_log_writer: csv.writer | None = None
        self._telemetry_log_started_at: datetime | None = None
        self._telemetry_log_current_path: Path | None = None
        self._telemetry_log_latest_path: Path | None = None
        self._telemetry_sample_count = 0
        self._telemetry_recording_scope = TelemetryRecordingScope.ALL
        self._telemetry_recording_actuator_id: int | None = None
        self._ensure_motion_directories()

    @property
    def system_status(self) -> SystemStatus:
        return SystemStatus(
            connection_state=self.gateway.connection_state,
            playback_status=self._playback_status,
            current_motion_name=self._current_motion_name,
            current_motion_category=self._current_motion_category,
            current_motion_loop=self._current_motion_loop,
            emulate_devices=self.settings.emulate_devices,
            telemetry_recording=self.telemetry_recording_status.is_recording,
            telemetry_log_name=self.telemetry_recording_status.current_log_name,
            telemetry_recording_scope=self.telemetry_recording_status.scope,
            telemetry_recording_actuator_id=self.telemetry_recording_status.actuator_id,
        )

    @property
    def telemetry_recording_status(self) -> TelemetryRecordingStatus:
        return TelemetryRecordingStatus(
            is_recording=self._telemetry_log_handle is not None,
            current_log_name=(
                self._telemetry_log_current_path.name
                if self._telemetry_log_current_path
                else None
            ),
            latest_log_name=(
                self._telemetry_log_latest_path.name
                if self._telemetry_log_latest_path
                else None
            ),
            started_at=self._telemetry_log_started_at,
            sample_count=self._telemetry_sample_count,
            scope=self._telemetry_recording_scope,
            actuator_id=self._telemetry_recording_actuator_id,
        )

    def list_actuators(self) -> list[ActuatorState]:
        return [actuator.model_copy(deep=True) for actuator in self._actuators]

    @property
    def has_complete_actuator_telemetry(self) -> bool:
        return len(self._telemetry_received_actuators) >= self.settings.actuator_count

    def has_fresh_actuator_telemetry(self, max_age_sec: float) -> bool:
        """Return true only when every actuator has reported recently."""
        now = self._time_fn()
        return all(
            received_at is not None and now - received_at <= max_age_sec
            for received_at in self._actuator_telemetry_received_at
        )

    def get_actuator(self, actuator_id: int) -> ActuatorState:
        return self._actuators[actuator_id].model_copy(deep=True)

    def list_leg_previews(self) -> list[LegPreview]:
        return [preview.model_copy(deep=True) for preview in build_leg_previews(self._actuators)]

    def get_leg_preview(self, leg_id: LegId) -> LegPreview:
        return build_leg_preview(leg_id, self._actuators).model_copy(deep=True)

    def list_motion_library(self) -> MotionLibrarySnapshot:
        return MotionLibrarySnapshot(
            fixed=self._list_motion_category(MotionCategory.FIXED),
            custom=self._list_motion_category(MotionCategory.CUSTOM),
        )

    def get_motion_file(self, category: MotionCategory, name: str) -> MotionFileDetail:
        path = self._motion_file_path(category, name)
        item, rows = self._read_motion_file(path, category)
        return MotionFileDetail(item=item, rows=rows)

    def import_legacy_csv(self, request: ImportLegacyCsvRequest) -> ImportedMotionDraft:
        (
            rows,
            interval_sec,
            loop,
            advance_mode,
            position_tolerance,
            pressure_threshold,
            step_timeout_sec,
            settle_time_sec,
            source_format,
        ) = self._parse_import_csv(request.content)
        suggested_name = self._sanitize_motion_name(request.name or "imported-motion")
        return ImportedMotionDraft(
            suggested_name=suggested_name,
            rows=rows,
            frame_count=len(rows),
            axis_count=max(len(row) for row in rows) if rows else 0,
            interval_sec=interval_sec,
            loop=loop,
            advance_mode=advance_mode,
            position_tolerance=position_tolerance,
            pressure_threshold=pressure_threshold,
            step_timeout_sec=step_timeout_sec,
            settle_time_sec=settle_time_sec,
            source_format=source_format,
        )

    def save_motion_file(
        self, category: MotionCategory, request: SaveMotionRequest
    ) -> MotionFileDetail:
        safe_name = self._sanitize_motion_name(request.name)
        path = self._category_path(category) / f"{safe_name}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(f"# interval_sec={request.interval_sec}\n")
            handle.write(f"# loop={'true' if request.loop else 'false'}\n")
            handle.write(f"# advance_mode={request.advance_mode.value}\n")
            handle.write(f"# position_tolerance={request.position_tolerance}\n")
            handle.write(f"# pressure_threshold={request.pressure_threshold}\n")
            handle.write(f"# step_timeout_sec={request.step_timeout_sec}\n")
            handle.write(f"# settle_time_sec={request.settle_time_sec}\n")
            writer = csv.writer(handle)
            for row in request.rows:
                writer.writerow([str(cell) for cell in row])
        item, rows = self._read_motion_file(path, category)
        return MotionFileDetail(item=item, rows=rows)

    def delete_motion_file(self, category: MotionCategory, name: str) -> None:
        if category is MotionCategory.FIXED:
            raise ValueError("Fixed motions cannot be deleted from the GUI")

        path = self._motion_file_path(category, name)
        path.unlink(missing_ok=False)

    async def connect(self) -> None:
        await self.gateway.connect()
        await self._prime_initial_gains()
        await self._publish_server_status()

    async def shutdown(self) -> None:
        await self.stop_csv_playback()
        self.stop_telemetry_recording()
        await self.gateway.disconnect()
        await self._publish_server_status()

    def start_telemetry_recording(
        self,
        request: StartTelemetryRecordingRequest | None = None,
    ) -> TelemetryRecordingStatus:
        if self._telemetry_log_handle is not None:
            return self.telemetry_recording_status

        request = request or StartTelemetryRecordingRequest()
        self.settings.telemetry_log_path.mkdir(parents=True, exist_ok=True)
        started_at = datetime.now(UTC)
        suffix = (
            f"actuator_{request.actuator_id}"
            if request.scope is TelemetryRecordingScope.SELECTED and request.actuator_id is not None
            else "all"
        )
        file_name = started_at.strftime(f"%Y%m%d_%H%M%S_telemetry_{suffix}.csv")
        log_path = self.settings.telemetry_log_path / file_name
        handle = log_path.open("w", encoding="utf-8", newline="", buffering=1)
        writer = csv.writer(handle)
        writer.writerow(
            [
                "timestamp_jst",
                "elapsed_ms",
                "actuator_id",
                "label",
                "position",
                "voltage",
                "command",
                "pressure",
                "target_position",
                "target_command",
            ]
        )

        self._telemetry_log_handle = handle
        self._telemetry_log_writer = writer
        self._telemetry_log_started_at = started_at
        self._telemetry_log_current_path = log_path
        self._telemetry_log_latest_path = log_path
        self._telemetry_sample_count = 0
        self._telemetry_recording_scope = request.scope
        self._telemetry_recording_actuator_id = request.actuator_id
        return self.telemetry_recording_status

    def stop_telemetry_recording(self) -> TelemetryRecordingStatus:
        if self._telemetry_log_handle is not None:
            self._telemetry_log_handle.flush()
            self._telemetry_log_handle.close()

        self._telemetry_log_handle = None
        self._telemetry_log_writer = None
        self._telemetry_log_started_at = None
        self._telemetry_log_current_path = None
        self._telemetry_sample_count = 0
        self._telemetry_recording_scope = TelemetryRecordingScope.ALL
        self._telemetry_recording_actuator_id = None
        return self.telemetry_recording_status

    def latest_telemetry_log_path(self) -> Path | None:
        return self._telemetry_log_latest_path

    def set_attitude_provider(self, provider: AttitudeProvider | None) -> None:
        """Late-bind the attitude source for the playback attitude guard.

        Exists because the app composition root builds `ControlService` before
        `SensorService` (see `main.py`); call this once both exist. Passing
        `None` disables the guard again regardless of `playback_attitude_guard_deg`.
        """
        self._attitude_provider = provider

    def set_level_offsets_provider(self, provider: LevelOffsetsProvider | None) -> None:
        self._level_offsets_provider = provider or (lambda: (0.0, 0.0))

    async def set_target(self, actuator_id: int, request: SetTargetRequest) -> ActuatorState:
        if self._motion_owner is not None:
            raise RuntimeError(
                f"{self._motion_owner} active — release it before manual control"
            )
        actuator = self._actuators[actuator_id]

        async with self._lock:
            state = self._actuators[actuator_id]
            # Update the BASE target only; stabilization corrections are composed
            # in at send time via `_effective_position_fields`.
            if request.mode is ControlMode.POSITION:
                self._commit_base_position_locked(state, request.value)
                fields = self._effective_position_fields(actuator.port_role)
                self._last_sent_position[actuator.port_role] = list(fields)
            else:
                state.target_command = request.value
                self._last_mode[actuator.port_role] = request.mode
                fields = self._current_fields_for_port(actuator.port_role, ControlMode.COMMAND)

        frame = build_set_target_frame(fields, request.mode)
        await self.gateway.send_frame(actuator.port_role, frame)

        await self._emit(
            "actuator_state",
            {"actuator": self._actuators[actuator_id].model_dump(mode="json")},
        )
        await self._emit_leg_preview_for_actuator(actuator_id)
        return self.get_actuator(actuator_id)

    async def request_gain(self, actuator_id: int) -> None:
        actuator = self._actuators[actuator_id]
        frame = build_request_gain_frame(actuator.local_index)
        await self.gateway.send_frame(actuator.port_role, frame)

    async def request_gain_save(self, actuator_id: int) -> None:
        actuator = self._actuators[actuator_id]
        frame = build_request_gain_save_frame(actuator.local_index)
        await self.gateway.send_frame(actuator.port_role, frame)

    async def _prime_initial_gains(self) -> None:
        for actuator in self._actuators:
            try:
                await self.request_gain(actuator.actuator_id)
            except ConnectionError as exc:
                logger.debug("Skipping initial gain request for %s: %s", actuator.label, exc)
                continue
            await asyncio.sleep(0.02)

    async def request_capture(self, actuator_id: int, request: CaptureRequest) -> None:
        actuator = self._actuators[actuator_id]
        frame = build_request_capture_frame(actuator.local_index, request.capture)
        await self.gateway.send_frame(actuator.port_role, frame)

    async def set_gain(self, actuator_id: int, request: SetGainRequest) -> None:
        actuator = self._actuators[actuator_id]
        frame = build_set_gain_frame(actuator.local_index, request.p, request.i, request.d)
        await self.gateway.send_frame(actuator.port_role, frame)

        async with self._lock:
            state = self._actuators[actuator_id]
            state.gains.p = int(request.p)
            state.gains.i = int(request.i)
            state.gains.d = int(request.d)
            state.updated_at = datetime.now(UTC)

        await self._emit(
            "actuator_state",
            {"actuator": self._actuators[actuator_id].model_dump(mode="json")},
        )

    async def start_fixed_motion(self, request: FixedMotionRequest) -> None:
        await self._emit("motion_request", {"motion": request.motion.value})

    async def start_csv_playback(self, request: CsvPlaybackRequest) -> None:
        if self._motion_owner is not None:
            raise RuntimeError(
                f"{self._motion_owner} active — release it before CSV playback"
            )
        await self.stop_csv_playback()
        self._playback_status = PlaybackStatus.RUNNING
        self._current_motion_name = request.motion_name
        self._current_motion_category = request.motion_category
        self._current_motion_loop = request.loop
        await self._emit("csv_playback_status", {"status": self._playback_status.value})
        await self._publish_server_status()
        self._csv_task = asyncio.create_task(self._run_csv_playback(request), name="csv-playback")

    async def start_home_motion(self) -> None:
        """Ramp from fresh measured positions to the tunable fixed home pose."""
        if self.system_status.connection_state is not ConnectionState.CONNECTED:
            raise RuntimeError("both actuator controllers must be connected before Home")
        if not self.settings.emulate_devices and not self.has_fresh_actuator_telemetry(
            self.settings.adaptive_walk_max_actuator_staleness_sec
        ):
            raise RuntimeError("fresh telemetry from all actuators is required before Home")

        detail = self.get_motion_file(MotionCategory.FIXED, "home")
        if not detail.rows:
            raise RuntimeError("Fixed Motion/home.csv must contain a target row")
        target = [int(value) for value in detail.rows[-1][: self.settings.actuator_count]]
        if len(target) != self.settings.actuator_count:
            raise RuntimeError("Fixed Motion/home.csv must contain all actuator targets")
        start = [item.telemetry.position for item in self.list_actuators()]
        rows = build_rate_limited_position_rows(
            start,
            target,
            max_rate=self.settings.home_motion_rate,
            interval_sec=self.settings.home_motion_interval_sec,
        )
        await self.start_csv_playback(
            CsvPlaybackRequest(
                rows=rows,
                interval_sec=self.settings.home_motion_interval_sec,
                loop=False,
                motion_name="home",
                motion_category=MotionCategory.FIXED,
            )
        )

    async def claim_adaptive_walking(self, owner: str = "adaptive walking") -> None:
        """Give a controller loop exclusive ownership of position targets."""
        await self.stop_csv_playback()
        async with self._lock:
            if self._motion_owner is not None:
                raise RuntimeError(f"{self._motion_owner} is already active")
            self._motion_owner = owner

    async def release_adaptive_walking(self) -> None:
        """Release target ownership while holding the last commanded posture."""
        async with self._lock:
            self._motion_owner = None

    async def apply_adaptive_walking_targets(self, targets: list[int]) -> None:
        """Atomically send one rate-limited walking target vector to both ESP32s."""
        if len(targets) != self.settings.actuator_count:
            raise ValueError("adaptive walking target count must match actuator_count")
        per_port_fields: dict[PortRole, list[int]] = {}
        async with self._lock:
            if self._motion_owner is None:
                raise RuntimeError("motion target ownership is not active")
            for index, value in enumerate(targets):
                clamped = max(POSITION_MIN, min(POSITION_MAX, int(value)))
                self._commit_base_position_locked(self._actuators[index], clamped)
            for port_role in (PortRole.FRONT, PortRole.BACK):
                fields = self._effective_position_fields(port_role)
                per_port_fields[port_role] = fields

        for port_role, fields in per_port_fields.items():
            frame = build_set_target_frame(fields, ControlMode.POSITION)
            await self.gateway.send_frame(port_role, frame)
            async with self._lock:
                self._last_sent_position[port_role] = list(fields)

    async def stop_csv_playback(self) -> None:
        if self._csv_task and not self._csv_task.done():
            self._playback_status = PlaybackStatus.STOPPING
            await self._emit("csv_playback_status", {"status": self._playback_status.value})
            await self._publish_server_status()
            self._csv_task.cancel()
            try:
                await self._csv_task
            except asyncio.CancelledError:
                pass
        self._csv_task = None
        self._playback_status = PlaybackStatus.IDLE
        self._current_motion_name = None
        self._current_motion_category = None
        self._current_motion_loop = False
        await self._emit("csv_playback_status", {"status": self._playback_status.value})
        await self._publish_server_status()

    async def handle_device_payload(self, envelope: DeviceEnvelope) -> None:
        try:
            raw_frame = decode_transport_payload(envelope.payload)
        except Exception:
            logger.debug("Failed to decode transport payload", exc_info=True)
            return
        decoded = decode_frame(raw_frame)
        if decoded is None:
            return

        if isinstance(decoded, SensorFrame):
            global_index = self._global_index(envelope.port_role, decoded.actuator_index)
            async with self._lock:
                actuator = self._actuators[global_index]
                actuator.telemetry.position = decoded.position
                actuator.telemetry.voltage = decoded.voltage
                actuator.telemetry.command = decoded.command
                actuator.telemetry.pressure = decoded.pressure
                actuator.updated_at = datetime.now(UTC)
                self._telemetry_received_actuators.add(global_index)
                self._actuator_telemetry_received_at[global_index] = self._time_fn()
                self._append_telemetry_log_row(actuator)
            await self._emit(
                "telemetry",
                {"actuator": self._actuators[global_index].model_dump(mode="json")},
            )
            await self._emit_leg_preview_for_actuator(global_index)
            return

        if isinstance(decoded, GainFrame):
            global_index = self._global_index(envelope.port_role, decoded.actuator_index)
            async with self._lock:
                actuator = self._actuators[global_index]
                actuator.gains.p = decoded.p_gain
                actuator.gains.i = decoded.i_gain
                actuator.gains.d = decoded.d_gain
                actuator.capture.max = decoded.capture_max
                actuator.capture.min = decoded.capture_min
                actuator.updated_at = datetime.now(UTC)
            await self._emit(
                "gain_response",
                {"actuator": self._actuators[global_index].model_dump(mode="json")},
            )

    async def _run_csv_playback(self, request: CsvPlaybackRequest) -> None:
        try:
            while True:
                for index, row in enumerate(request.rows):
                    self._playback_row_index = index
                    await self._apply_csv_row(row)
                    if request.advance_mode is PlaybackAdvanceMode.GUARDED:
                        await self._wait_for_row_ready(row, request)
                    else:
                        await asyncio.sleep(request.interval_sec)
                if not request.loop:
                    break
        except asyncio.CancelledError:
            raise
        finally:
            self._playback_status = PlaybackStatus.IDLE
            self._current_motion_name = None
            self._current_motion_category = None
            self._current_motion_loop = False
            self._playback_row_index = None
            await self._emit("csv_playback_status", {"status": self._playback_status.value})
            await self._publish_server_status()

    async def _apply_csv_row(self, row: list[str]) -> None:
        """Send one CSV playback row as BASE position targets.

        Targets are committed through `_commit_base_position_locked` -- the same
        storage `set_target` writes to -- so any active stabilization correction
        composes in automatically via `_effective_position_fields` before the
        frame is built. With stabilization disabled (all corrections == 0.0)
        the sent frames are byte-identical to the pre-Phase-3 base-only frames.
        """
        per_port_fields: dict[PortRole, list[int]] = {}
        async with self._lock:
            for global_index, value in enumerate(row[: self.settings.actuator_count]):
                if str(value).strip() == "":
                    continue
                self._commit_base_position_locked(self._actuators[global_index], int(value))

            # A frame is always sent for both ports (matching prior behaviour),
            # so both are marked POSITION-driven even if this particular row
            # left one of them untouched.
            for port_role in (PortRole.FRONT, PortRole.BACK):
                self._last_mode[port_role] = ControlMode.POSITION
                fields = self._effective_position_fields(port_role)
                per_port_fields[port_role] = fields
                self._last_sent_position[port_role] = list(fields)

        for port_role, fields in per_port_fields.items():
            frame = build_set_target_frame(fields, ControlMode.POSITION)
            await self.gateway.send_frame(port_role, frame)

    async def _wait_for_row_ready(self, row: list[str], request: CsvPlaybackRequest) -> None:
        active_targets: list[tuple[int, int]] = []
        for global_index, value in enumerate(row[: self.settings.actuator_count]):
            if str(value).strip() == "":
                continue
            active_targets.append((global_index, int(value)))

        if not active_targets:
            await asyncio.sleep(request.interval_sec)
            return

        start_time = asyncio.get_running_loop().time()
        ready_since: float | None = None

        while True:
            if self._row_ready(active_targets, request):
                now = asyncio.get_running_loop().time()
                if ready_since is None:
                    ready_since = now
                if now - ready_since >= request.settle_time_sec:
                    return
            else:
                ready_since = None

            elapsed = asyncio.get_running_loop().time() - start_time
            if elapsed >= request.step_timeout_sec:
                attitude_ok, roll_deg, pitch_deg = self._attitude_guard_state(request)
                await self._emit(
                    "playback_guard",
                    {
                        "status": "timeout",
                        "targets": [index for index, _ in active_targets],
                        "position_tolerance": request.position_tolerance,
                        "pressure_threshold": request.pressure_threshold,
                        "attitude_hold": not attitude_ok,
                        "roll_deg": roll_deg,
                        "pitch_deg": pitch_deg,
                    },
                )
                return

            await asyncio.sleep(max(0.02, min(request.interval_sec, 0.05)))

    def _row_ready(
        self, active_targets: list[tuple[int, int]], request: CsvPlaybackRequest
    ) -> bool:
        for actuator_index, target in active_targets:
            actuator = self._actuators[actuator_index]
            position_error = abs(actuator.telemetry.position - target)
            if position_error > request.position_tolerance:
                return False
            if (
                request.pressure_threshold > 0
                and actuator.telemetry.pressure < request.pressure_threshold
            ):
                return False
        attitude_ok, _roll_deg, _pitch_deg = self._attitude_guard_state(request)
        return attitude_ok

    def _attitude_guard_threshold_deg(self, request: CsvPlaybackRequest) -> float | None:
        if request.attitude_guard_deg is not None:
            return request.attitude_guard_deg
        return self.settings.playback_attitude_guard_deg

    def _attitude_guard_state(
        self, request: CsvPlaybackRequest
    ) -> tuple[bool, float | None, float | None]:
        """Optional attitude condition ANDed into `_row_ready` (Phase 3).

        Returns ``(ok, roll_deg, pitch_deg)``. ``ok`` is True whenever the guard
        is disabled (no threshold configured -- neither per-request nor via
        `Settings.playback_attitude_guard_deg` -- or no attitude provider is
        wired up) or the level-corrected tilt is within the threshold. It is
        False while the tilt exceeds the threshold, and also while the attitude
        snapshot is missing or stale: the caller's own `step_timeout_sec` bound
        (identical to the pre-existing position/pressure guards) is what
        prevents an indefinite hold in that case, so a dead/disconnected IMU
        can never hang playback forever.
        """
        threshold = self._attitude_guard_threshold_deg(request)
        if threshold is None or threshold <= 0.0 or self._attitude_provider is None:
            return True, None, None

        snapshot = self._attitude_provider()
        if snapshot is None:
            return False, None, None
        # Reuse the stabilization staleness bound: same underlying question
        # ("is this attitude snapshot still trustworthy?"), single tunable.
        if self._time_fn() - snapshot.timestamp > self.settings.stabilization_max_staleness_sec:
            return False, None, None

        offset_roll, offset_pitch = self._level_offsets_provider()
        roll = snapshot.euler.roll_deg - offset_roll
        pitch = snapshot.euler.pitch_deg - offset_pitch
        ok = abs(roll) <= threshold and abs(pitch) <= threshold
        return ok, roll, pitch

    def _build_initial_actuators(self, actuator_count: int) -> list[ActuatorState]:
        labels = [
            "front right hip",
            "front right knee",
            "front left hip",
            "front left knee",
            "rear right hip",
            "rear right knee",
            "rear left hip",
            "rear left knee",
        ]
        actuators: list[ActuatorState] = []
        for actuator_id in range(actuator_count):
            port_role = (
                PortRole.FRONT
                if actuator_id < NUM_ESP32_CONTROLLED_ACTUATORS
                else PortRole.BACK
            )
            local_index = actuator_id % NUM_ESP32_CONTROLLED_ACTUATORS
            label = labels[actuator_id] if actuator_id < len(labels) else f"actuator-{actuator_id}"
            actuators.append(
                ActuatorState(
                    actuator_id=actuator_id,
                    label=label,
                    port_role=port_role,
                    local_index=local_index,
                )
            )
        return actuators

    def _ensure_motion_directories(self) -> None:
        self.settings.fixed_motion_path.mkdir(parents=True, exist_ok=True)
        self.settings.custom_motion_path.mkdir(parents=True, exist_ok=True)
        self.settings.telemetry_log_path.mkdir(parents=True, exist_ok=True)

    async def publish_server_status(self) -> None:
        await self._publish_server_status()

    async def publish_motion_library(self) -> None:
        await self._emit("motion_library", self.list_motion_library().model_dump(mode="json"))

    async def _publish_server_status(self) -> None:
        await self._emit("server_status", self.system_status.model_dump(mode="json"))

    def _append_telemetry_log_row(self, actuator: ActuatorState) -> None:
        if self._telemetry_log_writer is None or self._telemetry_log_started_at is None:
            return
        if (
            self._telemetry_recording_scope is TelemetryRecordingScope.SELECTED
            and actuator.actuator_id != self._telemetry_recording_actuator_id
        ):
            return

        elapsed_ms = int(
            (actuator.updated_at - self._telemetry_log_started_at).total_seconds() * 1000
        )
        self._telemetry_log_writer.writerow(
            [
                self._format_jst_timestamp(actuator.updated_at),
                elapsed_ms,
                actuator.actuator_id,
                actuator.label,
                actuator.telemetry.position,
                actuator.telemetry.voltage,
                actuator.telemetry.command,
                actuator.telemetry.pressure,
                actuator.target_position,
                actuator.target_command,
            ]
        )
        self._telemetry_sample_count += 1

    def _format_jst_timestamp(self, timestamp: datetime) -> str:
        local_dt = timestamp.astimezone(JST)
        return local_dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + " JST"

    def _category_path(self, category: MotionCategory) -> Path:
        if category is MotionCategory.FIXED:
            return self.settings.fixed_motion_path
        return self.settings.custom_motion_path

    def _list_motion_category(self, category: MotionCategory) -> list[MotionLibraryItem]:
        items: list[MotionLibraryItem] = []
        for path in sorted(self._category_path(category).glob("*.csv")):
            try:
                item, _ = self._read_motion_file(path, category)
            except ValueError:
                continue
            items.append(item)
        items.sort(key=lambda item: item.name.lower())
        return items

    def _motion_file_path(self, category: MotionCategory, name: str) -> Path:
        safe_name = self._sanitize_motion_name(name)
        path = (self._category_path(category) / f"{safe_name}.csv").resolve()
        base = self._category_path(category).resolve()
        if base not in path.parents and path != base:
            raise ValueError("Invalid motion path")
        if not path.exists():
            raise ValueError(f"Motion '{name}' was not found")
        return path

    def _read_motion_file(
        self, path: Path, category: MotionCategory
    ) -> tuple[MotionLibraryItem, list[list[str]]]:
        with path.open("r", encoding="utf-8", newline="") as handle:
            (
                rows,
                interval_sec,
                loop,
                advance_mode,
                position_tolerance,
                pressure_threshold,
                step_timeout_sec,
                settle_time_sec,
            ) = self._parse_motion_text(handle.read())

        if not rows:
            raise ValueError(f"Motion file '{path.name}' does not contain any frames")

        axis_count = max(len(row) for row in rows)
        item = MotionLibraryItem(
            name=path.stem,
            category=category,
            file_name=path.name,
            frame_count=len(rows),
            axis_count=axis_count,
            interval_sec=interval_sec,
            loop=loop,
            advance_mode=advance_mode,
            position_tolerance=position_tolerance,
            pressure_threshold=pressure_threshold,
            step_timeout_sec=step_timeout_sec,
            settle_time_sec=settle_time_sec,
            updated_at=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC),
        )
        return item, rows

    def _parse_legacy_csv_rows(self, content: str) -> list[list[str]]:
        text = content.lstrip("\ufeff")
        reader = csv.reader(io.StringIO(text, newline=""))
        header_row = next(reader, None)
        if header_row is None:
            raise ValueError("CSV file is empty")

        rows: list[list[str]] = []
        for row in reader:
            rows.append([value for value in row])

        if not rows:
            raise ValueError("CSV file does not contain any motion rows after the header")

        return rows

    def _parse_import_csv(
        self, content: str
    ) -> tuple[
        list[list[str]],
        float,
        bool,
        PlaybackAdvanceMode,
        int,
        int,
        float,
        float,
        str,
    ]:
        text = content.lstrip("\ufeff")
        non_empty_lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not non_empty_lines:
            raise ValueError("CSV file is empty")

        if any(line.startswith("#") for line in non_empty_lines):
            (
                rows,
                interval_sec,
                loop,
                advance_mode,
                position_tolerance,
                pressure_threshold,
                step_timeout_sec,
                settle_time_sec,
            ) = self._parse_motion_text(text)
            return (
                rows,
                interval_sec,
                loop,
                advance_mode,
                position_tolerance,
                pressure_threshold,
                step_timeout_sec,
                settle_time_sec,
                "modern",
            )

        rows = self._parse_legacy_csv_rows(text)
        return (
            rows,
            1.0 / 30.0,
            False,
            PlaybackAdvanceMode.TIME,
            160,
            0,
            1.5,
            0.1,
            "legacy",
        )

    def _parse_motion_text(
        self, content: str
    ) -> tuple[list[list[str]], float, bool, PlaybackAdvanceMode, int, int, float, float]:
        interval_sec: float | None = None
        loop = False
        advance_mode = PlaybackAdvanceMode.TIME
        position_tolerance = 160
        pressure_threshold = 0
        step_timeout_sec = 1.5
        settle_time_sec = 0.1
        rows: list[list[str]] = []

        for raw_line in content.lstrip("\ufeff").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("#"):
                key, _, value = line[1:].partition("=")
                key = key.strip()
                value = value.strip()
                if key == "interval_sec":
                    try:
                        interval_sec = float(value)
                    except ValueError:
                        interval_sec = None
                elif key == "loop":
                    loop = value.lower() == "true"
                elif key == "advance_mode":
                    try:
                        advance_mode = PlaybackAdvanceMode(value.lower())
                    except ValueError:
                        advance_mode = PlaybackAdvanceMode.TIME
                elif key == "position_tolerance":
                    try:
                        position_tolerance = int(value)
                    except ValueError:
                        position_tolerance = 160
                elif key == "pressure_threshold":
                    try:
                        pressure_threshold = int(value)
                    except ValueError:
                        pressure_threshold = 0
                elif key == "step_timeout_sec":
                    try:
                        step_timeout_sec = float(value)
                    except ValueError:
                        step_timeout_sec = 1.5
                elif key == "settle_time_sec":
                    try:
                        settle_time_sec = float(value)
                    except ValueError:
                        settle_time_sec = 0.1
                continue

            rows.append([cell.strip() for cell in next(csv.reader([line]))])

        if not rows:
            raise ValueError("CSV file does not contain any motion rows")

        return (
            rows,
            interval_sec if interval_sec is not None else 1.0 / 30.0,
            loop,
            advance_mode,
            position_tolerance,
            pressure_threshold,
            step_timeout_sec,
            settle_time_sec,
        )

    def _sanitize_motion_name(self, name: str) -> str:
        sanitized = re.sub(r"[^\w\- ]+", "_", name.strip())
        sanitized = sanitized.strip(" .")
        if not sanitized:
            raise ValueError("Motion name is empty")
        return sanitized

    async def apply_stabilization_corrections(self, corrections: list[float]) -> None:
        """Apply per-actuator position-target corrections from the stabilizer.

        Composes ``effective = clamp(base + correction)`` and re-sends only the
        ports whose effective targets moved past the deadband, and only while the
        port is in POSITION mode (a command-mode port is skipped, never clobbered).
        Called at the stabilization loop rate (~25 Hz); serial writes are bounded
        to at most one frame per port per tick.
        """
        deadband = self.settings.stabilization_correction_deadband
        ports_to_send: dict[PortRole, list[int]] = {}
        async with self._lock:
            for index in range(min(len(corrections), len(self._corrections))):
                self._corrections[index] = float(corrections[index])

            for port_role in (PortRole.FRONT, PortRole.BACK):
                if self._last_mode[port_role] is not ControlMode.POSITION:
                    continue
                fields = self._effective_position_fields(port_role)
                last = self._last_sent_position[port_role]
                if self._max_abs_diff(fields, last) >= deadband:
                    ports_to_send[port_role] = fields

        # Send outside the lock. Only record a port as "sent" after the write
        # actually succeeds so a failed send is retried on the next tick (and the
        # stabilizer's consecutive-failure auto-disable can fire).
        for port_role, fields in ports_to_send.items():
            frame = build_set_target_frame(fields, ControlMode.POSITION)
            await self.gateway.send_frame(port_role, frame)
            async with self._lock:
                self._last_sent_position[port_role] = list(fields)

    def _commit_base_position_locked(self, actuator: ActuatorState, value: int) -> None:
        """Set a single actuator's BASE position target. Caller must hold `_lock`.

        Shared by `set_target` (single actuator) and `_apply_csv_row` (batch)
        so both paths funnel into the same base-target storage; stabilization
        corrections are composed on top at send time via
        `_effective_position_fields`.
        """
        actuator.target_position = value
        self._last_mode[actuator.port_role] = ControlMode.POSITION

    def _base_position_fields(self, port_role: PortRole) -> list[int]:
        start = 0 if port_role is PortRole.FRONT else NUM_ESP32_CONTROLLED_ACTUATORS
        end = start + NUM_ESP32_CONTROLLED_ACTUATORS
        return [actuator.target_position for actuator in self._actuators[start:end]]

    def _effective_target_for_index(self, index: int) -> int:
        """Single actuator's effective position target: clamp(base + round(corr)).

        Sole authority for the base+correction composition. Both the frame-send
        path (`_effective_position_fields`) and the experiment sampler
        (`sample_control_snapshot`) route through this so the recorded
        `effective_target` is identical to what is actually commanded.
        """
        effective = self._actuators[index].target_position + int(round(self._corrections[index]))
        return max(POSITION_MIN, min(POSITION_MAX, effective))

    def _effective_position_fields(self, port_role: PortRole) -> list[int]:
        start = 0 if port_role is PortRole.FRONT else NUM_ESP32_CONTROLLED_ACTUATORS
        end = start + NUM_ESP32_CONTROLLED_ACTUATORS
        return [self._effective_target_for_index(index) for index in range(start, end)]

    async def sample_control_snapshot(self) -> ControlSample:
        """Coherent control-plane snapshot of all actuators for experiment logging.

        Builds every row under a single `_lock` acquisition so base target,
        correction, effective target, actual position and pressure are mutually
        consistent (no mid-tick interleaving with a correction or telemetry
        update).
        """
        async with self._lock:
            rows = tuple(
                ControlSampleRow(
                    actuator_id=actuator.actuator_id,
                    label=actuator.label,
                    control_mode=self._last_mode[actuator.port_role].value,
                    actual_position=actuator.telemetry.position,
                    base_target=actuator.target_position,
                    effective_target=self._effective_target_for_index(actuator.actuator_id),
                    correction=self._corrections[actuator.actuator_id],
                    pressure=actuator.telemetry.pressure,
                )
                for actuator in self._actuators
            )
            return ControlSample(
                rows=rows,
                playback_row_index=self._playback_row_index,
                motion_name=self._current_motion_name,
            )

    @staticmethod
    def _max_abs_diff(a: list[int], b: list[int]) -> int:
        return max((abs(x - y) for x, y in zip(a, b, strict=True)), default=0)

    def _current_fields_for_port(self, port_role: PortRole, mode: ControlMode) -> list[int]:
        start = 0 if port_role is PortRole.FRONT else NUM_ESP32_CONTROLLED_ACTUATORS
        end = start + NUM_ESP32_CONTROLLED_ACTUATORS
        fields: list[int] = []
        for actuator in self._actuators[start:end]:
            if mode is ControlMode.POSITION:
                fields.append(actuator.target_position)
            else:
                fields.append(actuator.target_command)
        return fields

    def _global_index(self, port_role: PortRole, local_index: int) -> int:
        if port_role is PortRole.FRONT:
            return local_index
        return local_index + NUM_ESP32_CONTROLLED_ACTUATORS

    async def _emit(self, event_type: str, payload: dict[str, object]) -> None:
        await self.event_sink(TelemetryEvent(type=event_type, payload=payload))

    async def _emit_leg_preview_for_actuator(self, actuator_id: int) -> None:
        leg_id = leg_id_for_actuator(actuator_id)
        if leg_id is None:
            return
        await self._emit(
            "leg_preview", {"leg": self.get_leg_preview(leg_id).model_dump(mode="json")}
        )
