"""Pneumatic response approximation and per-joint adaptive phase compensation."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from highend_server.simulation.config import (
    AdaptiveControlConfig,
    PneumaticActuatorConfig,
)


@dataclass(slots=True)
class PneumaticActuator:
    """Delay + asymmetric rate limit + first-order pressure response + joint PD.

    The state called ``pressure_target_rad`` is not literal pressure. It is the
    equivalent unloaded joint angle produced by the current cylinder pressure.
    This compact model is useful before valve/pressure/chamber data is known and
    remains straightforward to identify from real position logs.
    """

    config: PneumaticActuatorConfig
    pressure_target_rad: float = 0.0
    delayed_target_rad: float = 0.0
    _commands: deque[tuple[float, float]] = field(default_factory=deque)

    def reset(self, angle_rad: float = 0.0) -> None:
        self.pressure_target_rad = angle_rad
        self.delayed_target_rad = angle_rad
        self._commands.clear()

    def command(self, target_rad: float, now_s: float) -> None:
        self._commands.append((now_s, target_rad))

    def update(
        self,
        now_s: float,
        dt: float,
        joint_angle_rad: float,
        joint_velocity: float,
    ) -> float:
        cutoff = now_s - self.config.delay_s
        while self._commands and self._commands[0][0] <= cutoff:
            _, self.delayed_target_rad = self._commands.popleft()

        error = self.delayed_target_rad - self.pressure_target_rad
        if abs(error) <= self.config.deadband_rad:
            response_velocity = 0.0
        else:
            rate_limit = (
                self.config.extend_speed_rad_s
                if error > 0
                else self.config.retract_speed_rad_s
            )
            response_velocity = max(
                -self.config.retract_speed_rad_s,
                min(rate_limit, error / self.config.time_constant_s),
            )
        self.pressure_target_rad += response_velocity * dt

        torque = (
            self.config.kp_nm_rad * (self.pressure_target_rad - joint_angle_rad)
            - self.config.kd_nm_s_rad * joint_velocity
        )
        return max(-self.config.max_torque_nm, min(self.config.max_torque_nm, torque))


@dataclass(slots=True)
class AdaptiveTrackingController:
    """Online phase-lead learning for one actuator.

    A pneumatic joint typically trails a periodic target. For a locally smooth
    trajectory, tracking error is approximately ``delay * target_velocity``.
    The normalized update below learns that delay separately for every joint,
    then commands a future point on the trajectory. This is intentionally a
    bounded, low-authority adaptation suitable for later transfer to hardware.
    """

    config: AdaptiveControlConfig
    lead_s: float | None = None

    def __post_init__(self) -> None:
        if self.lead_s is None:
            self.lead_s = self.config.initial_lead_s

    def reset(self) -> None:
        self.lead_s = self.config.initial_lead_s

    def command(
        self,
        desired_angle: float,
        desired_velocity: float,
        measured_angle: float,
        dt: float,
    ) -> float:
        tracking_error = desired_angle - measured_angle
        if self.config.enabled and abs(desired_velocity) > 0.03:
            normalized_gradient = (
                tracking_error * desired_velocity
                / (desired_velocity * desired_velocity + 0.04)
            )
            assert self.lead_s is not None
            self.lead_s += self.config.learning_rate * normalized_gradient * dt
            self.lead_s = max(0.0, min(self.config.max_lead_s, self.lead_s))

        lead = self.lead_s if self.config.enabled else 0.0
        return (
            desired_angle
            + lead * desired_velocity
            + self.config.feedback_gain * tracking_error
        )
