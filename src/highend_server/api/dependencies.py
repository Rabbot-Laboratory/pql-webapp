from fastapi import Request

from highend_server.application.control_service import ControlService
from highend_server.config import Settings


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_control_service(request: Request) -> ControlService:
    return request.app.state.control_service

