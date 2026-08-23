from fastapi import Request

from highend_server.application.adaptive_walking import AdaptiveWalkingController
from highend_server.application.control_service import ControlService
from highend_server.application.experiment import ExperimentRecorder
from highend_server.application.hardware_status import HardwareStatusService
from highend_server.application.stabilization import StabilizationController
from highend_server.application.standing import StandingController
from highend_server.config import Settings
from highend_server.input.gamepad_service import GamepadService
from highend_server.sensors.sensor_service import SensorService


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_control_service(request: Request) -> ControlService:
    return request.app.state.control_service


def get_sensor_service(request: Request) -> SensorService:
    return request.app.state.sensor_service


def get_gamepad_service(request: Request) -> GamepadService:
    return request.app.state.gamepad_service


def get_hardware_status_service(request: Request) -> HardwareStatusService:
    return request.app.state.hardware_status_service


def get_stabilization_controller(request: Request) -> StabilizationController:
    return request.app.state.stabilization_controller


def get_adaptive_walking_controller(request: Request) -> AdaptiveWalkingController:
    return request.app.state.adaptive_walking_controller


def get_standing_controller(request: Request) -> StandingController:
    return request.app.state.standing_controller


def get_experiment_recorder(request: Request) -> ExperimentRecorder:
    return request.app.state.experiment_recorder
