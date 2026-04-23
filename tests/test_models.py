from pydantic import ValidationError

from highend_server.domain.models import COMMAND_MAX, COMMAND_NEUTRAL, ControlMode, SetTargetRequest


def test_command_targets_accept_1800_range() -> None:
    request = SetTargetRequest(mode=ControlMode.COMMAND, value=COMMAND_MAX)
    assert request.value == COMMAND_MAX


def test_command_targets_reject_values_above_1800() -> None:
    try:
        SetTargetRequest(mode=ControlMode.COMMAND, value=COMMAND_MAX + 1)
    except ValidationError:
        return
    raise AssertionError("command range validation did not reject the out-of-range value")


def test_command_neutral_default_is_900() -> None:
    assert COMMAND_NEUTRAL == 900
