from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HIGHEND_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Highend Control Server"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_reload: bool = False
    serial_baudrate: int = 115200
    serial_timeout_sec: float = Field(default=1.0, gt=0.0)
    serial_write_timeout_sec: float = Field(default=1.0, gt=0.0)
    front_port_name: str = "/dev/ttyUSB-Front"
    back_port_name: str = "/dev/ttyUSB-Back"
    require_all_ports: bool = False
    emulate_devices: bool = False
    emulate_tick_interval_sec: float = Field(default=0.05, gt=0.0)
    actuator_count: int = 8
    websocket_ping_interval_sec: float = Field(default=15.0, gt=0.0)
    hardware_status_interval_sec: float = Field(default=0.5, gt=0.0)
    hardware_imu_stale_sec: float = Field(default=1.0, gt=0.0)
    # Gamepad observation is isolated from actuator output. Local evdev input
    # must be explicitly enabled on the Pi; browser input is display/log only.
    gamepad_local_enabled: bool = False
    gamepad_web_enabled: bool = True
    gamepad_device_path: str = ""
    gamepad_name_match: str = "F710"
    gamepad_input_timeout_sec: float = Field(default=0.2, gt=0.0)
    gamepad_publish_interval_sec: float = Field(default=0.05, gt=0.0)
    csv_default_interval_sec: float = Field(default=1.0 / 30.0, gt=0.0)
    allowed_origin_regex: str = r"https?://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+)(:\d+)?"
    motion_root_dir: str = "Motion"
    fixed_motion_dir_name: str = "Fixed Motion"
    custom_motion_dir_name: str = "Custom Motion"
    telemetry_log_root_dir: str = "Logs"
    telemetry_log_dir_name: str = "telemetry"
    sensors_enabled: bool = False
    # ADC read + WebSocket publish cadence on the asyncio side (backward-compatible
    # key; still used to pace ADC reads and the gyro-zero sampling sleep).
    sensor_poll_interval_sec: float = Field(default=0.05, gt=0.0)
    # WebSocket sensor_state publish cadence (decimated view of the IMU thread).
    sensor_publish_interval_sec: float = Field(default=0.05, gt=0.0)
    # Dedicated IMU thread sample rate (accel + gyro) and the lower magnetometer
    # read rate (BMM150 cannot sustain 100 Hz).
    imu_sample_rate_hz: float = Field(default=100.0, gt=0.0)
    imu_mag_sample_rate_hz: float = Field(default=20.0, gt=0.0)
    # Mahony complementary-filter gains (see sensors/attitude.py).
    mahony_kp: float = Field(default=0.8, ge=0.0)
    mahony_ki: float = Field(default=0.02, ge=0.0)
    # Bounded ring buffer size for magnetometer calibration collection.
    mag_calibration_max_samples: int = Field(default=2000, ge=1)
    sensor_i2c_bus: int = 1
    bmx055_accel_address: int = 0x18
    bmx055_gyro_address: int = 0x68
    bmx055_mag_address: int = 0x10
    adc_spi_bus: int = 0
    # One 5 V MCP3208 on CE0 exposes all eight installation-sensor channels.
    # SPI logic is level-shifted between the Pi (3.3 V) and ADC (5 V) by TXU0304.
    adc_spi_devices: str = "0"
    adc_spi_max_speed_hz: int = 1_000_000
    adc_vref: float = Field(default=5.0, gt=0.0)
    sensor_config_dir_name: str = "config"
    imu_calibration_file_name: str = "imu_calibration.json"
    contact_calibration_file_name: str = "contact_calibration.json"

    # --- Attitude stabilization (Phase 2 feedback control) -----------------
    # Correction-send / control loop rate. Kept well below the 100 Hz fusion
    # thread: pneumatic actuators respond slowly, so faster control is useless
    # and can excite oscillation. 25 Hz is a safe default (see risk table).
    stabilization_rate_hz: float = Field(default=25.0, gt=0.0, le=200.0)
    # Max absolute per-actuator correction (position units, 0..4095 scale).
    # Small by default so the loop can never command a large motion on hardware.
    stabilization_max_correction: float = Field(default=120.0, ge=0.0)
    # Test-derived sensor-to-control signs.  The 2026-07-11 manual IMU trial
    # observed right-side-down as negative raw Roll and nose-up as positive raw
    # Pitch.  The feedback controller uses +Roll=right-low, +Pitch=nose-up.
    # Keep these deliberately separate from the raw IMU/API convention.
    stabilization_roll_sign: Literal[-1, 1] = -1
    stabilization_pitch_sign: Literal[-1, 1] = 1
    # Max change in a correction per second (position units/sec) while active.
    stabilization_max_correction_rate: float = Field(default=400.0, gt=0.0)
    # Auto-disable if |roll| or |pitch| (level-corrected) exceeds this.
    stabilization_max_tilt_deg: float = Field(default=30.0, gt=0.0)
    # Auto-disable if the latest attitude snapshot is older than this (sec).
    stabilization_max_staleness_sec: float = Field(default=0.2, gt=0.0)
    # On disable (manual or auto) corrections ramp to zero over ~this long.
    stabilization_disable_ramp_sec: float = Field(default=0.5, gt=0.0)
    # Consecutive serial send failures that trigger auto-disable.
    stabilization_serial_failure_limit: int = Field(default=5, ge=1)
    # Suppress a per-port re-send when no effective target moved this much.
    stabilization_correction_deadband: float = Field(default=4.0, ge=0.0)
    # Anti-windup clamp on each axis PID integral accumulator (deg*sec).
    stabilization_integral_limit: float = Field(default=40.0, ge=0.0)
    stabilization_config_file_name: str = "stabilization.json"

    # --- CSV playback attitude guard (Phase 3 motion-correction integration) --
    # Optional server-wide default (degrees) for the GUARDED-mode row-advance
    # attitude check: while set, a row does not advance until the level-corrected
    # |roll| and |pitch| are both within this threshold (ANDed with the existing
    # position/pressure guards). None (default) disables the check entirely, so
    # playback behaviour is unchanged unless explicitly opted into. Overridable
    # per playback request via `CsvPlaybackRequest.attitude_guard_deg`.
    playback_attitude_guard_deg: float | None = Field(default=None, ge=0.0)

    # --- Experiment logging (per-run directory under Logs/experiments/) -----
    experiment_log_dir_name: str = "experiments"
    # Sampler rate for the experiment telemetry CSV. 25 Hz matches the
    # stabilization loop (corrections change at most 25x/s) and keeps SD-card
    # IO on the Pi to ~200 rows/s (~200 MB/h in long format). Raise only for
    # short high-resolution runs.
    experiment_sample_rate_hz: float = Field(default=25.0, gt=0.0, le=100.0)
    # CSV/JSONL flush cadence. Never line-buffer the experiment CSV: at
    # 200 rows/s per-line flushes would hammer the SD card.
    experiment_flush_interval_sec: float = Field(default=1.0, gt=0.0)
    experiment_robot_name: str = "PQL-A00"

    # --- Replay mode (python -m highend_server --replay <experiment_dir>) ---
    # When set, SensorService feeds the recorded IMU stream from the given
    # experiment directory instead of real/emulated hardware.
    replay_dir: str | None = None
    replay_time_scale: float = Field(default=1.0, gt=0.0)

    # --- Emulated IMU scenario (HIGHEND_EMULATED_IMU_SCENARIO) --------------
    # Profile used by EmulatedImuSource. "smooth" is the legacy sinusoid
    # behaviour; see sensors/imu_scenarios.py for the full catalogue
    # (static, roll-step, pitch-step, diagonal-step, impulse, oscillation,
    # gyro-bias, accel-disturbance, sensor-stale, sensor-nan).
    emulated_imu_scenario: str = "smooth"

    # --- Stabilization derivative source ------------------------------------
    # "error_difference": D = d(attitude error)/dt (finite difference, legacy).
    # "gyro_rate": D uses the bias-corrected gyro rate directly
    # (roll: -gyro_x, pitch: -gyro_y) — less noise amplification, no
    # double-differentiation of the fused angle. Runtime-selectable so A/B
    # comparison on real hardware uses identical code paths.
    stabilization_derivative_source: Literal["error_difference", "gyro_rate"] = (
        "error_difference"
    )

    # --- Adaptive forward walking (real-hardware conservative defaults) ----
    # The browser renews a short lease while the forward button is held. A lost
    # pointer-up/network/browser closes the lease and stops target updates.
    adaptive_walk_rate_hz: float = Field(default=25.0, gt=0.0, le=100.0)
    adaptive_walk_lease_timeout_sec: float = Field(default=0.45, gt=0.1, le=2.0)
    adaptive_walk_max_imu_staleness_sec: float = Field(default=0.2, gt=0.0, le=1.0)
    adaptive_walk_max_actuator_staleness_sec: float = Field(
        default=0.5, gt=0.0, le=5.0
    )
    adaptive_walk_max_tilt_deg: float = Field(default=12.0, gt=0.0, le=45.0)
    # Fixed Motion CSV played by the walk button (gait_lab candidates:
    # walk_crawl / walk_trot_slow / walk_rabbit_v3 / walk_pronk / walk_bound_lowamp).
    adaptive_walk_motion_name: str = Field(default="rabbit_bound", min_length=1)
    # Rear-driven rabbit bound reaches the full known motion range gradually;
    # the independent target-rate limit remains the final hardware guard.
    adaptive_walk_motion_scale: float = Field(default=1.0, gt=0.0, le=1.0)
    adaptive_walk_motion_ramp_sec: float = Field(default=2.0, gt=0.0, le=30.0)
    adaptive_walk_learning_rate: float = Field(default=0.08, ge=0.0, le=10.0)
    adaptive_walk_feedback_gain: float = Field(default=0.10, ge=0.0, le=1.0)
    adaptive_walk_initial_phase_lead_s: float = Field(default=0.04, ge=0.0, le=0.5)
    adaptive_walk_max_phase_lead_s: float = Field(default=0.20, ge=0.0, le=0.5)
    adaptive_walk_velocity_regularizer: float = Field(default=10_000.0, gt=0.0)
    adaptive_walk_max_phase_offset: float = Field(default=60.0, ge=0.0, le=500.0)
    adaptive_walk_attitude_kp: float = Field(default=2.0, ge=0.0, le=50.0)
    adaptive_walk_attitude_kd: float = Field(default=0.25, ge=0.0, le=50.0)
    adaptive_walk_trim_rate: float = Field(default=0.20, ge=0.0, le=20.0)
    adaptive_walk_trim_leak_rate: float = Field(default=0.02, ge=0.0, le=2.0)
    adaptive_walk_max_trim: float = Field(default=30.0, ge=0.0, le=500.0)
    adaptive_walk_max_attitude_correction: float = Field(default=60.0, ge=0.0, le=500.0)
    adaptive_walk_max_target_rate: float = Field(default=1200.0, gt=0.0, le=5000.0)
    # --- Contact-aware walking (all off by default: verified-hardware only) --
    # Master switch: gate gait-phase progression and mask attitude corrections
    # by the debounced foot-contact states. Enable only after the per-leg
    # contact calibration has been verified on the real robot.
    adaptive_walk_use_contact: bool = False
    # Gait phase (0..1 of the motion CSV cycle) at which the rear-leg kick
    # starts; phase progression pauses there until both rear legs report
    # contact (or the timeout elapses). None disables the gate.
    adaptive_walk_kick_gate_phase: float | None = Field(default=None, ge=0.0, lt=1.0)
    adaptive_walk_gate_timeout_sec: float = Field(default=0.3, gt=0.0, le=2.0)
    # End of the rear-leg kick window (for pitch-proportional thrust scaling).
    adaptive_walk_kick_end_phase: float | None = Field(default=None, gt=0.0, le=1.0)
    # Raibert-style heuristic: scale the rear-axis kick amplitude by
    # (1 + gain * pitch_error_deg), clamped to [0.7, 1.3]. Sign and magnitude
    # must be tuned on hardware; 0 disables.
    adaptive_walk_pitch_thrust_gain: float = Field(default=0.0, ge=-1.0, le=1.0)
    # --- Iterative learning control (off by default) ------------------------
    # Per-cycle feed-forward: previous cycle's per-frame tracking error shapes
    # the next cycle's nominal waveform. Updates are kept only when the cycle
    # RMS error improved (see application/ilc.py).
    adaptive_walk_ilc_gain: float = Field(default=0.0, ge=0.0, le=2.0)
    adaptive_walk_ilc_max: float = Field(default=100.0, ge=0.0, le=500.0)
    # Header Home button: ramp every axis toward Fixed Motion/home.csv instead
    # of jumping directly to the stored pose.
    home_motion_rate: float = Field(default=150.0, gt=0.0, le=2000.0)
    home_motion_interval_sec: float = Field(default=0.04, gt=0.01, le=0.2)

    @property
    def project_root(self) -> Path:
        return Path(__file__).resolve().parents[2]

    @property
    def motion_root_path(self) -> Path:
        return self.project_root / self.motion_root_dir

    @property
    def fixed_motion_path(self) -> Path:
        return self.motion_root_path / self.fixed_motion_dir_name

    @property
    def custom_motion_path(self) -> Path:
        return self.motion_root_path / self.custom_motion_dir_name

    @property
    def telemetry_log_root_path(self) -> Path:
        return self.project_root / self.telemetry_log_root_dir

    @property
    def telemetry_log_path(self) -> Path:
        return self.telemetry_log_root_path / self.telemetry_log_dir_name

    @property
    def sensor_config_path(self) -> Path:
        return self.project_root / self.sensor_config_dir_name

    @property
    def imu_calibration_path(self) -> Path:
        return self.sensor_config_path / self.imu_calibration_file_name

    @property
    def contact_calibration_path(self) -> Path:
        return self.sensor_config_path / self.contact_calibration_file_name

    @property
    def stabilization_config_path(self) -> Path:
        return self.sensor_config_path / self.stabilization_config_file_name

    @property
    def experiment_log_root_path(self) -> Path:
        return self.telemetry_log_root_path / self.experiment_log_dir_name


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
