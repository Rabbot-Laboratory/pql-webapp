from functools import lru_cache
from pathlib import Path

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
    adc_spi_devices: str = "0,1"
    adc_spi_max_speed_hz: int = 1_000_000
    adc_vref: float = Field(default=3.3, gt=0.0)
    sensor_config_dir_name: str = "config"
    imu_calibration_file_name: str = "imu_calibration.json"

    # --- Attitude stabilization (Phase 2 feedback control) -----------------
    # Correction-send / control loop rate. Kept well below the 100 Hz fusion
    # thread: pneumatic actuators respond slowly, so faster control is useless
    # and can excite oscillation. 25 Hz is a safe default (see risk table).
    stabilization_rate_hz: float = Field(default=25.0, gt=0.0, le=200.0)
    # Max absolute per-actuator correction (position units, 0..4095 scale).
    # Small by default so the loop can never command a large motion on hardware.
    stabilization_max_correction: float = Field(default=120.0, ge=0.0)
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
    def stabilization_config_path(self) -> Path:
        return self.sensor_config_path / self.stabilization_config_file_name


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
