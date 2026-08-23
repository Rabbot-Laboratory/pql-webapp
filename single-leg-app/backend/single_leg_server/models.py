from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class ControlMode(StrEnum):
    POSITION = "position"
    COMMAND = "command"


class ConnectionState(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    EMULATED = "emulated"


class ActuatorTelemetry(BaseModel):
    position: int = 2048
    voltage: int = 0
    command: int = 900
    pressure: int = 0


class GainValues(BaseModel):
    p: int | None = None
    i: int | None = None
    d: int | None = None


class CaptureValues(BaseModel):
    min: int | None = None
    max: int | None = None


class ActuatorState(BaseModel):
    actuator_id: int
    label: str
    port_role: str = "Single Leg"
    local_index: int
    telemetry: ActuatorTelemetry = Field(default_factory=ActuatorTelemetry)
    target_position: int = 2048
    target_command: int = 900
    gains: GainValues = Field(default_factory=GainValues)
    capture: CaptureValues = Field(default_factory=CaptureValues)
    updated_at: datetime = Field(default_factory=utc_now)


class SystemStatus(BaseModel):
    server_ok: bool = True
    connection_state: ConnectionState = ConnectionState.CONNECTING
    emulate_devices: bool = False
    esp32_path: str
    updated_at: datetime = Field(default_factory=utc_now)


class TargetRequest(BaseModel):
    mode: ControlMode
    value: int = Field(ge=0, le=4095)

    @model_validator(mode="after")
    def validate_command_range(self) -> TargetRequest:
        if self.mode is ControlMode.COMMAND and self.value > 1800:
            raise ValueError("command target must be between 0 and 1800")
        return self


class GainRequest(BaseModel):
    p: int = Field(ge=0, le=255)
    i: int = Field(ge=0, le=255)
    d: int = Field(ge=0, le=255)


class CaptureRequest(BaseModel):
    capture: str


class TelemetryEvent(BaseModel):
    type: str
    timestamp: datetime = Field(default_factory=utc_now)
    payload: dict
