from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field, model_validator

POSITION_MIN = 0
POSITION_MAX = 4095
COMMAND_MIN = 0
COMMAND_MAX = 1800
COMMAND_NEUTRAL = 900


def utc_now() -> datetime:
    return datetime.now(UTC)


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


class SensorConnectionState(str, Enum):
    DISABLED = "disabled"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


class HardwareConnectionState(str, Enum):
    DISABLED = "disabled"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    MISSING = "missing"
    ERROR = "error"
    STALE = "stale"
    EMULATED = "emulated"


class GamepadSource(str, Enum):
    NONE = "none"
    LOCAL = "local"
    WEB = "web"


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
    robot_ready: bool = False


class SerialPortState(BaseModel):
    port_role: PortRole
    path: str
    connection_state: ConnectionState = ConnectionState.DISCONNECTED
    error: str | None = None
    last_received_at: datetime | None = None
    updated_at: datetime = Field(default_factory=utc_now)


class HardwareDeviceStatus(BaseModel):
    device_id: str
    label: str
    kind: str
    required: bool = True
    enabled: bool = True
    connection_state: HardwareConnectionState
    detail: str | None = None
    path: str | None = None
    last_seen_at: datetime | None = None
    updated_at: datetime = Field(default_factory=utc_now)


class HardwareStatus(BaseModel):
    server_ok: bool = True
    robot_ready: bool = False
    required_connected: int = 0
    required_total: int = 0
    devices: list[HardwareDeviceStatus] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=utc_now)


class ImuVector(BaseModel):
    x: float
    y: float
    z: float


class ImuOrientation(BaseModel):
    roll_deg: float
    pitch_deg: float
    yaw_deg: float | None = None


class ImuQuaternion(BaseModel):
    w: float
    x: float
    y: float
    z: float


class ImuCalibration(BaseModel):
    level_roll_deg: float = 0.0
    level_pitch_deg: float = 0.0
    gyro_offset_dps: ImuVector = Field(default_factory=lambda: ImuVector(x=0.0, y=0.0, z=0.0))
    # Magnetometer hard-iron offset and diagonal soft-iron scale. Defaults are
    # identity (offset 0, scale 1) so calibration files predating these fields
    # load with no magnetometer correction applied.
    mag_offset: ImuVector = Field(default_factory=lambda: ImuVector(x=0.0, y=0.0, z=0.0))
    mag_scale: ImuVector = Field(default_factory=lambda: ImuVector(x=1.0, y=1.0, z=1.0))
    updated_at: datetime = Field(default_factory=utc_now)


class MagCalibrationQuality(BaseModel):
    sample_count: int
    residual: float
    coverage: float
    offset: ImuVector
    scale: ImuVector


class Bmx055State(BaseModel):
    connection_state: SensorConnectionState = SensorConnectionState.DISABLED
    error: str | None = None
    quaternion: ImuQuaternion | None = None
    accel_g: ImuVector | None = None
    gyro_dps: ImuVector | None = None
    mag_raw: ImuVector | None = None
    gravity_g: ImuVector | None = None
    linear_accel_g: ImuVector | None = None
    raw_orientation: ImuOrientation | None = None
    orientation: ImuOrientation | None = None
    calibration: ImuCalibration = Field(default_factory=ImuCalibration)
    temperature_c: float | None = None
    # Additive telemetry for the 100 Hz pipeline / magnetometer calibration flow.
    sample_count: int = 0
    mag_calibration_active: bool = False
    mag_calibration_samples: int = 0
    updated_at: datetime = Field(default_factory=utc_now)


class AdcChannelState(BaseModel):
    bank: int
    channel: int
    raw: int | None = None
    voltage: float | None = None


class AdcBankState(BaseModel):
    bus: int
    device: int
    connection_state: SensorConnectionState = SensorConnectionState.DISABLED
    error: str | None = None
    channels: list[AdcChannelState] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=utc_now)


class ContactPolarity(str, Enum):
    ACTIVE_HIGH = "active_high"
    ACTIVE_LOW = "active_low"


class ContactLegCalibration(BaseModel):
    leg: LegId
    channel: int = Field(ge=0, le=7)
    # Hysteresis pair: supporting turns ON at on_threshold and OFF at
    # off_threshold.  active_high requires on >= off; active_low the reverse.
    on_threshold: int = Field(default=2048, ge=0, le=4095)
    off_threshold: int = Field(default=2048, ge=0, le=4095)
    polarity: ContactPolarity = ContactPolarity.ACTIVE_HIGH

    @model_validator(mode="after")
    def _thresholds_consistent(self) -> ContactLegCalibration:
        if self.polarity is ContactPolarity.ACTIVE_HIGH:
            if self.on_threshold < self.off_threshold:
                raise ValueError("active_high requires on_threshold >= off_threshold")
        elif self.on_threshold > self.off_threshold:
            raise ValueError("active_low requires on_threshold <= off_threshold")
        return self


def _default_contact_legs() -> list[ContactLegCalibration]:
    # Provisional CH0-3 wiring map carried over from the display-only panel.
    # Verify each channel on the real robot before contact is used by control.
    return [
        ContactLegCalibration(leg=leg, channel=channel)
        for channel, leg in enumerate(
            (LegId.FRONT_RIGHT, LegId.FRONT_LEFT, LegId.REAR_RIGHT, LegId.REAR_LEFT)
        )
    ]


class ContactCalibration(BaseModel):
    device: int = 0
    debounce_ticks: int = Field(default=2, ge=1, le=50)
    legs: list[ContactLegCalibration] = Field(default_factory=_default_contact_legs)

    @model_validator(mode="after")
    def _one_entry_per_leg(self) -> ContactCalibration:
        legs = [item.leg for item in self.legs]
        if sorted(legs) != sorted(LegId):
            raise ValueError("calibration must contain exactly one entry per leg")
        return self


class ContactLegState(BaseModel):
    leg: LegId
    raw: int | None = None
    voltage: float | None = None
    supporting: bool = False


class SensorState(BaseModel):
    enabled: bool = False
    imu: Bmx055State = Field(default_factory=Bmx055State)
    adc_banks: list[AdcBankState] = Field(default_factory=list)
    contact: list[ContactLegState] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=utc_now)


class GamepadState(BaseModel):
    source: GamepadSource = GamepadSource.NONE
    connected: bool = False
    stale: bool = True
    device_name: str | None = None
    mapping: str | None = None
    axes: dict[str, float] = Field(default_factory=dict)
    buttons: dict[str, bool] = Field(default_factory=dict)
    raw_axes: dict[str, float] = Field(default_factory=dict)
    raw_buttons: dict[str, float] = Field(default_factory=dict)
    deadman: bool = False
    updated_at: datetime = Field(default_factory=utc_now)


class WebGamepadUpdate(BaseModel):
    connected: bool = True
    id: str = Field(default="browser gamepad", max_length=200)
    index: int = Field(default=0, ge=0, le=15)
    mapping: str = Field(default="", max_length=50)
    axes: list[float] = Field(default_factory=list, max_length=16)
    buttons: list[float] = Field(default_factory=list, max_length=32)


class ImuCalibrationRequest(BaseModel):
    sample_count: int = Field(default=60, ge=1, le=300)


class SetTargetRequest(BaseModel):
    mode: ControlMode
    value: int = Field(ge=POSITION_MIN, le=POSITION_MAX)

    @model_validator(mode="after")
    def validate_mode_specific_range(self) -> SetTargetRequest:
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
    # Phase 3: optional per-motion override for the attitude row-advance guard
    # (degrees). None (default) falls back to `Settings.playback_attitude_guard_deg`;
    # if that is also None the guard is disabled and behaviour is unchanged from
    # pre-Phase-3 playback.
    attitude_guard_deg: float | None = Field(default=None, ge=0.0)


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
    def validate_scope_requirements(self) -> StartTelemetryRecordingRequest:
        if self.scope is TelemetryRecordingScope.SELECTED and self.actuator_id is None:
            raise ValueError("actuator_id is required when scope=selected")
        return self


class StabilizationGains(BaseModel):
    """Per-axis PID gains for attitude stabilization.

    Defaults are deliberately conservative / near-minimal so the loop is gentle
    on real hardware out of the box; on-robot tuning is expected. Integral gains
    default to 0 (pure PD) to avoid any boot-time windup; enable a small ki
    (~0.02-0.05) only after P/D behaviour is validated on the robot.
    """

    kp_roll: float = Field(default=1.5, ge=0.0, le=100.0)
    ki_roll: float = Field(default=0.0, ge=0.0, le=50.0)
    kd_roll: float = Field(default=0.3, ge=0.0, le=100.0)
    kp_pitch: float = Field(default=1.5, ge=0.0, le=100.0)
    ki_pitch: float = Field(default=0.0, ge=0.0, le=50.0)
    kd_pitch: float = Field(default=0.3, ge=0.0, le=100.0)


class StabilizationCorrection(BaseModel):
    actuator_id: int
    label: str
    # Position-target offset (0..4095 units) added on top of the base target.
    correction: float = 0.0


class StabilizationState(BaseModel):
    enabled: bool = False
    # active == user-enabled AND healthy (not auto-disabled, fresh attitude).
    active: bool = False
    auto_disabled: bool = False
    disabled_reason: str | None = None
    gains: StabilizationGains = Field(default_factory=StabilizationGains)
    roll_deg: float = 0.0
    pitch_deg: float = 0.0
    roll_error_deg: float = 0.0
    pitch_error_deg: float = 0.0
    corrections: list[StabilizationCorrection] = Field(default_factory=list)
    loop_rate_hz: float = 0.0
    attitude_stale: bool = False
    # "error_difference" (legacy finite-difference D-term) or "gyro_rate"
    # (D-term sourced from the bias-corrected gyro). Additive/backward
    # compatible: existing clients that ignore this field are unaffected.
    derivative_source: str = "error_difference"
    updated_at: datetime = Field(default_factory=utc_now)


class StabilizationRequest(BaseModel):
    enabled: bool | None = None
    gains: StabilizationGains | None = None


class StandingPhase(str, Enum):
    OFF = "off"
    RISING = "rising"
    HOLDING = "holding"


class StandingRequest(BaseModel):
    enabled: bool
    safety_confirmed: bool = False


class StandingState(BaseModel):
    enabled: bool = False
    phase: StandingPhase = StandingPhase.OFF
    standing_ok: bool = False
    walk_gate_enabled: bool = True
    auto_disabled: bool = False
    disabled_reason: str | None = None
    roll_deg: float = 0.0
    pitch_deg: float = 0.0
    axis_errors: list[int] = Field(default_factory=list)
    overdrive_active: list[bool] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=utc_now)


class AdaptiveWalkMode(str, Enum):
    ADAPTIVE = "adaptive"
    # Pure nominal-trajectory replay (rate limit only): the baseline
    # measurement mode for "run exactly one cycle with adaptation off".
    REPLAY = "replay"


class AdaptiveWalkRequest(BaseModel):
    pressed: bool
    safety_confirmed: bool = False
    # When set, the walk auto-stops (and auto-records an experiment) after
    # this many full-amplitude gait cycles. Cycles start counting after the
    # amplitude ramp completes.
    cycles: int | None = Field(default=None, ge=1, le=10)
    mode: AdaptiveWalkMode = AdaptiveWalkMode.ADAPTIVE


class HomePoseRequest(BaseModel):
    safety_confirmed: bool = False


class AdaptiveWalkState(BaseModel):
    active: bool = False
    auto_stopped: bool = False
    stopped_reason: str | None = None
    phase: float = Field(default=0.0, ge=0.0, le=1.0)
    roll_deg: float = 0.0
    pitch_deg: float = 0.0
    imu_stale: bool = True
    motion_scale: float = Field(default=0.0, ge=0.0, le=1.0)
    lease_remaining_ms: int = Field(default=0, ge=0)
    roll_trim: float = 0.0
    pitch_trim: float = 0.0
    learned_phase_lead_s: list[float] = Field(default_factory=list)
    mode: AdaptiveWalkMode = AdaptiveWalkMode.ADAPTIVE
    cycle_count: int = 0
    target_cycles: int | None = None
    gate_waiting: bool = False
    saturated_axes: list[bool] = Field(default_factory=list)
    rate_limited_axes: list[bool] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=utc_now)


class ExperimentStartRequest(BaseModel):
    experiment_type: str = Field(min_length=1, max_length=64)
    name: str | None = Field(default=None, max_length=128)


class ExperimentNoteRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class ExperimentGitInfo(BaseModel):
    sha: str = "unknown"
    branch: str = "unknown"
    dirty: bool | None = None


class ExperimentManifest(BaseModel):
    schema_version: int = 1
    experiment_id: str
    experiment_type: str
    name: str | None = None
    robot: str = "PQL-A00"
    package_version: str = "unknown"
    git: ExperimentGitInfo = Field(default_factory=ExperimentGitInfo)
    started_at: datetime
    ended_at: datetime | None = None
    sample_rate_hz: float
    stabilization: dict
    imu: dict
    config_snapshot: dict
    row_counts: dict | None = None


class ExperimentSummary(BaseModel):
    manifest: ExperimentManifest
    directory: str
    duration_sec: float
    telemetry_rows: int
    event_count: int
    telemetry_bytes: int


class ExperimentStatus(BaseModel):
    running: bool
    experiment_id: str | None = None
    directory: str | None = None
    elapsed_sec: float | None = None
    telemetry_rows: int = 0
    event_count: int = 0


class TelemetryEvent(BaseModel):
    type: str
    timestamp: datetime = Field(default_factory=utc_now)
    payload: dict


@dataclass(slots=True)
class DeviceEnvelope:
    port_role: PortRole
    payload: bytes
