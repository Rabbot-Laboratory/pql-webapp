from fastapi import Request

from highend_server.application.control_service import ControlService
from highend_server.application.stabilization import StabilizationController
from highend_server.config import Settings
from highend_server.sensors.sensor_service import SensorService


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_control_service(request: Request) -> ControlService:
    return request.app.state.control_service


def get_sensor_service(request: Request) -> SensorService:
    return request.app.state.sensor_service


def get_stabilization_controller(request: Request) -> StabilizationController:
    return request.app.state.stabilization_controller
