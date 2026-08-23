"""Named motion/fault scenarios for :class:`EmulatedImuSource`.

Each scenario is a pure function ``elapsed_seconds -> ScenarioSample``: the
target attitude for that instant plus optional fault-injection flags. Keeping
these here (instead of inline in ``sensor_service.py``) lets the demo/test
suite exercise the *pipeline* (calibration, Mahony fusion, staleness/NaN
guards, stabilization control loop) against realistic, repeatable inputs
without touching real hardware.

``EmulatedImuSource`` (see ``sensor_service.py``) turns a ``ScenarioSample``
into a full 9-axis reading: attitude -> quaternion -> gravity -> accel (plus
``accel_extra_g`` and legacy sinusoid noise), attitude -> mag (rotated world
field), and a finite-differenced gyro (plus ``gyro_bias_dps``). ``inject_nan``
and ``hold_stale`` are handled entirely in ``EmulatedImuSource.read()`` (the
scenario just flags the cycle).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from math import exp, pi, sin

from highend_server.sensors.imu_bmx055 import Vector3

_ZERO = Vector3(0.0, 0.0, 0.0)


@dataclass(slots=True, frozen=True)
class ScenarioSample:
    """Target attitude + fault-injection flags for one instant."""

    roll_deg: float
    pitch_deg: float
    yaw_deg: float
    accel_extra_g: Vector3 = field(default_factory=lambda: _ZERO)
    gyro_bias_dps: Vector3 = field(default_factory=lambda: _ZERO)
    baseline_accel_scale: float = 1.0
    inject_nan: bool = False
    hold_stale: bool = False


ScenarioFn = Callable[[float], ScenarioSample]


def _step_window(elapsed: float, start: float, end: float, magnitude: float) -> float:
    """``magnitude`` inside ``[start, end)``, else 0.0 — the shared step shape."""
    return magnitude if start <= elapsed < end else 0.0


def _smooth(elapsed: float) -> ScenarioSample:
    # Legacy waveform (moved verbatim from the old EmulatedImuSource.read()):
    # continuous roll/pitch sinusoids plus an unwrapped yaw ramp so the
    # quaternion never wraps discontinuously. This is the default scenario —
    # existing tests that assume this motion must keep passing unchanged.
    roll_deg = 12.0 * sin(elapsed * 0.85)
    pitch_deg = 8.0 * sin(elapsed * 0.57 + 0.9)
    yaw_deg = elapsed * 20.0
    return ScenarioSample(roll_deg=roll_deg, pitch_deg=pitch_deg, yaw_deg=yaw_deg)


def _static(_elapsed: float) -> ScenarioSample:
    # Stable baseline for `--demo`: exactly level, without synthetic ripple.
    return ScenarioSample(
        roll_deg=0.0,
        pitch_deg=0.0,
        yaw_deg=0.0,
        baseline_accel_scale=0.0,
    )


def _roll_step(elapsed: float) -> ScenarioSample:
    # t<2s: 0 deg. 2s<=t<5s: +10 deg roll step. t>=5s: back to 0 deg.
    roll_deg = _step_window(elapsed, 2.0, 5.0, 10.0)
    return ScenarioSample(roll_deg=roll_deg, pitch_deg=0.0, yaw_deg=0.0)


def _pitch_step(elapsed: float) -> ScenarioSample:
    # Same 2s/5s timing window as roll-step, but on pitch (+8 deg).
    pitch_deg = _step_window(elapsed, 2.0, 5.0, 8.0)
    return ScenarioSample(roll_deg=0.0, pitch_deg=pitch_deg, yaw_deg=0.0)


def _diagonal_step(elapsed: float) -> ScenarioSample:
    # Roll (+8 deg) and pitch (+6 deg) step together in the same 2s-5s window
    # — exercises roll/pitch cross-coupling in the mixing matrix.
    roll_deg = _step_window(elapsed, 2.0, 5.0, 8.0)
    pitch_deg = _step_window(elapsed, 2.0, 5.0, 6.0)
    return ScenarioSample(roll_deg=roll_deg, pitch_deg=pitch_deg, yaw_deg=0.0)


def _impulse(elapsed: float) -> ScenarioSample:
    # 0 deg roll until t=3s, then a sharp +15 deg pulse that decays
    # exponentially (tau = 0.25s): roll = 15 * exp(-4*(t-3)) for t>=3.
    if elapsed >= 3.0:
        roll_deg = 15.0 * exp(-4.0 * (elapsed - 3.0))
    else:
        roll_deg = 0.0
    return ScenarioSample(roll_deg=roll_deg, pitch_deg=0.0, yaw_deg=0.0)


def _oscillation(elapsed: float) -> ScenarioSample:
    # Pure 0.5 Hz roll oscillation, +/-10 deg — a control-bandwidth probe.
    roll_deg = 10.0 * sin(2.0 * pi * 0.5 * elapsed)
    return ScenarioSample(roll_deg=roll_deg, pitch_deg=0.0, yaw_deg=0.0)


def _gyro_bias(_elapsed: float) -> ScenarioSample:
    # Level attitude forever, but the gyro carries a constant bias — probes
    # Mahony ki (integral gyro-bias rejection): the fused attitude should
    # stay near level even though the raw gyro never reads zero.
    return ScenarioSample(
        roll_deg=0.0,
        pitch_deg=0.0,
        yaw_deg=0.0,
        gyro_bias_dps=Vector3(1.5, -0.8, 0.5),
    )


def _accel_disturbance(elapsed: float) -> ScenarioSample:
    # Attitude stays level throughout; a forward (+X) 0.35g accel pulse fires
    # during 3s-4.5s with no matching attitude change — reproduces "does
    # Mahony mistake translation for tilt?" (it should not, since the
    # disturbance is far shorter than the filter's correction time constant).
    extra_x = _step_window(elapsed, 3.0, 4.5, 0.35)
    return ScenarioSample(
        roll_deg=0.0,
        pitch_deg=0.0,
        yaw_deg=0.0,
        accel_extra_g=Vector3(extra_x, 0.0, 0.0),
    )


def _sensor_stale(elapsed: float) -> ScenarioSample:
    # Smooth motion until t=5s, then hold_stale=True forever: the sensor
    # "wedges" and stops producing fresh reads (see EmulatedImuSource.read()
    # and the module docstring in sensor_service.py for the exact mechanism).
    if elapsed < 5.0:
        return _smooth(elapsed)
    sample = _smooth(elapsed)
    return ScenarioSample(
        roll_deg=sample.roll_deg,
        pitch_deg=sample.pitch_deg,
        yaw_deg=sample.yaw_deg,
        hold_stale=True,
    )


def _sensor_nan(elapsed: float) -> ScenarioSample:
    # Smooth motion until t=5s, then inject_nan=True forever: proves the
    # isfinite guards in MahonyMARG.update() hold the last good attitude
    # instead of propagating NaN.
    sample = _smooth(elapsed)
    if elapsed < 5.0:
        return sample
    return ScenarioSample(
        roll_deg=sample.roll_deg,
        pitch_deg=sample.pitch_deg,
        yaw_deg=sample.yaw_deg,
        inject_nan=True,
    )


SCENARIOS: dict[str, ScenarioFn] = {
    "smooth": _smooth,
    "static": _static,
    "roll-step": _roll_step,
    "pitch-step": _pitch_step,
    "diagonal-step": _diagonal_step,
    "impulse": _impulse,
    "oscillation": _oscillation,
    "gyro-bias": _gyro_bias,
    "accel-disturbance": _accel_disturbance,
    "sensor-stale": _sensor_stale,
    "sensor-nan": _sensor_nan,
}


def get_scenario(name: str) -> ScenarioFn:
    """Look up a scenario by name.

    Raises ``ValueError`` (not ``KeyError``) so callers building from
    untrusted config/env values get a message listing the valid names.
    """
    try:
        return SCENARIOS[name]
    except KeyError:
        available = ", ".join(sorted(SCENARIOS))
        raise ValueError(
            f"unknown IMU scenario {name!r}; available scenarios: {available}"
        ) from None
