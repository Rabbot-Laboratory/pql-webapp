from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for the one-ESP32 bench application."""

    model_config = SettingsConfigDict(
        env_prefix="SINGLE_LEG_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Single Leg Control"
    api_host: str = "0.0.0.0"
    api_port: int = 8100
    port_name: str = "/dev/ttyUSB-Leg"
    serial_baudrate: int = 115200
    serial_timeout_sec: float = Field(default=0.2, gt=0.0)
    serial_write_timeout_sec: float = Field(default=1.0, gt=0.0)
    reconnect_interval_sec: float = Field(default=1.0, gt=0.0)
    emulate_devices: bool = False
    emulate_tick_interval_sec: float = Field(default=0.05, gt=0.0)
    unused_position: int = Field(default=2048, ge=0, le=4095)
    unused_command: int = Field(default=900, ge=0, le=4095)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

