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
    sensor_poll_interval_sec: float = Field(default=0.05, gt=0.0)
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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
