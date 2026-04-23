from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field, model_validator


POSITION_MIN = 0
POSITION_MAX = 4095
COMMAND_MIN = 0
COMMAND_MAX = 1800
COMMAND_NEUTRAL = 900


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PortRole(str, Enum):
    FRONT = "Front"
    BACK = "Back"


class ControlMode(str, Enum):
    POSITION = "position"
    COMMAND = "command"


class LegId(str, Enum):
    FRONT_RIGHT = "front_right"
    FRONT_LEFT = "front_left"
    REAR_RIGHT = "rear_right"
    REAR_LEFT = "rear_left"


class FixedMotion(str, Enum):
    CRAWL = "crawl"
    TROT = "trot"
    PACE = "pace"
    BOUND = "bound"


class MotionCategory(str, Enum):
    FIXED = "fixed"
    CUSTOM = "custom"


class TelemetryRecordingScope(str, Enum):
    ALL = "all"
    SELECTED = "selected"


class PlaybackAdvanceMode(str, Enum):
    TIME = "time"
    GUARDED = "guarded"


class PlaybackStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    STOPPING = "stopping"


class ConnectionState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


class GainValues(BaseModel):
    p: int | None = None
    i: int | None = None
    d: int | None = None


class CaptureValues(BaseModel):
    min: int | None = None
    max: int | None = None


class ActuatorTelemetry(BaseModel):
    position: int = 2048
    voltage: int = 0
    command: int = COMMAND_NEUTRAL
    pressure: int = 0


class ActuatorState(BaseModel):
    actuator_id: int
    label: str
    port_role: PortRole
    local_index: int
    telemetry: ActuatorTelemetry = Field(default_factory=ActuatorTelemetry)
    target_position: int = 2048
    target_command: int = COMMAND_NEUTRAL
    gains: GainValues = Field(default_factory=GainValues)
    capture: CaptureValues = Field(default_factory=CaptureValues)
    updated_at: datetime = Field(default_factory=utc_now)


class JointPreview(BaseModel):
    actuator_id: int
    label: str
    joint_name: str
    position: int
    angle_rad: float
    target_position: int
    target_angle_rad: float
    command: int


class LegPreview(BaseModel):
    leg_id: LegId
    label: str
    fixed_joint_name: str
    fixed_joint_angle_rad: float = 0.0
    mirror_x: bool = False
    hip: JointPreview
    knee: JointPreview
    updated_at: datetime = Field(default_factory=utc_now)


class SystemStatus(BaseModel):
    connection_state: ConnectionState
    playback_status: PlaybackStatus
    current_motion_name: str | None = None
    current_motion_category: MotionCategory | None = None
    current_motion_loop: bool = False
    emulate_devices: bool
    telemetry_recording: bool = False
    telemetry_log_name: str | None = None
    telemetry_recording_scope: TelemetryRecordingScope = TelemetryRecordingScope.ALL
    telemetry_recording_actuator_id: int | None = None
    updated_at: datetime = Field(default_factory=utc_now)


class HealthResponse(BaseModel):
    ok: bool
    service: str
    system: SystemStatus


class SetTargetRequest(BaseModel):
    mode: ControlMode
    value: int = Field(ge=POSITION_MIN, le=POSITION_MAX)

    @model_validator(mode="after")
    def validate_mode_specific_range(self) -> "SetTargetRequest":
        if self.mode is ControlMode.COMMAND and not (COMMAND_MIN <= self.value <= COMMAND_MAX):
            raise ValueError(f"Command targets must be between {COMMAND_MIN} and {COMMAND_MAX}")
        return self


class SetGainRequest(BaseModel):
    p: float = Field(ge=0, le=255)
    i: float = Field(ge=0, le=255)
    d: float = Field(ge=0, le=255)


class CaptureRequest(BaseModel):
    capture: str = Field(pattern="^(offset|stroke)$")


class FixedMotionRequest(BaseModel):
    motion: FixedMotion


class CsvPlaybackRequest(BaseModel):
    rows: list[list[str]]
    interval_sec: float = Field(default=1.0 / 30.0, gt=0.0)
    loop: bool = False
    motion_name: str | None = None
    motion_category: MotionCategory | None = None
    advance_mode: PlaybackAdvanceMode = PlaybackAdvanceMode.TIME
    position_tolerance: int = Field(default=160, ge=0, le=POSITION_MAX)
    pressure_threshold: int = Field(default=0, ge=0, le=POSITION_MAX)
    step_timeout_sec: float = Field(default=1.5, gt=0.0)
    settle_time_sec: float = Field(default=0.1, ge=0.0)


class MotionLibraryItem(BaseModel):
    name: str
    category: MotionCategory
    file_name: str
    frame_count: int
    axis_count: int
    interval_sec: float | None = None
    loop: bool = False
    advance_mode: PlaybackAdvanceMode = PlaybackAdvanceMode.TIME
    position_tolerance: int = 160
    pressure_threshold: int = 0
    step_timeout_sec: float = 1.5
    settle_time_sec: float = 0.1
    updated_at: datetime


class MotionLibrarySnapshot(BaseModel):
    fixed: list[MotionLibraryItem]
    custom: list[MotionLibraryItem]


class MotionFileDetail(BaseModel):
    item: MotionLibraryItem
    rows: list[list[str]]


class ImportedMotionDraft(BaseModel):
    suggested_name: str
    rows: list[list[str]]
    frame_count: int
    axis_count: int
    interval_sec: float = 1.0 / 30.0
    loop: bool = False
    advance_mode: PlaybackAdvanceMode = PlaybackAdvanceMode.TIME
    position_tolerance: int = 160
    pressure_threshold: int = 0
    step_timeout_sec: float = 1.5
    settle_time_sec: float = 0.1
    source_format: str = "legacy"


class SaveMotionRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    rows: list[list[str]]
    interval_sec: float = Field(default=1.0 / 30.0, gt=0.0)
    loop: bool = False
    advance_mode: PlaybackAdvanceMode = PlaybackAdvanceMode.TIME
    position_tolerance: int = Field(default=160, ge=0, le=POSITION_MAX)
    pressure_threshold: int = Field(default=0, ge=0, le=POSITION_MAX)
    step_timeout_sec: float = Field(default=1.5, gt=0.0)
    settle_time_sec: float = Field(default=0.1, ge=0.0)


class ImportLegacyCsvRequest(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    content: str = Field(min_length=1)


class TelemetryRecordingStatus(BaseModel):
    is_recording: bool
    current_log_name: str | None = None
    latest_log_name: str | None = None
    started_at: datetime | None = None
    sample_count: int = 0
    scope: TelemetryRecordingScope = TelemetryRecordingScope.ALL
    actuator_id: int | None = None


class StartTelemetryRecordingRequest(BaseModel):
    scope: TelemetryRecordingScope = TelemetryRecordingScope.ALL
    actuator_id: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_scope_requirements(self) -> "StartTelemetryRecordingRequest":
        if self.scope is TelemetryRecordingScope.SELECTED and self.actuator_id is None:
            raise ValueError("actuator_id is required when scope=selected")
        return self


class TelemetryEvent(BaseModel):
    type: str
    timestamp: datetime = Field(default_factory=utc_now)
    payload: dict


@dataclass(slots=True)
class DeviceEnvelope:
    port_role: PortRole
    payload: bytes
