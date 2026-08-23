"""Configuration for the simulated pneumatic actuators and walking controller."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

JOINT_NAMES: tuple[str, ...] = (
    "rev_fr2",
    "rev_fr3",
    "rev_fl2",
    "rev_fl3",
    "rev_rr2",
    "rev_rr3",
    "rev_rl2",
    "rev_rl3",
)


@dataclass(frozen=True, slots=True)
class PneumaticActuatorConfig:
    """A deliberately small, identifiable approximation of one air cylinder.

    ``extend_speed_rad_s`` and ``retract_speed_rad_s`` are joint-side speeds.
    They can be obtained directly from a recorded step response without knowing
    cylinder bore, linkage leverage, valve flow coefficients, or hose volume.
    """

    delay_s: float
    extend_speed_rad_s: float
    retract_speed_rad_s: float
    time_constant_s: float
    deadband_rad: float
    kp_nm_rad: float
    kd_nm_s_rad: float
    max_torque_nm: float


@dataclass(frozen=True, slots=True)
class GaitConfig:
    cycle_s: float = 2.2
    duty_factor: float = 0.75
    stride_m: float = 0.100
    lift_m: float = 0.040
    front_stride_scale: float = 1.70
    rear_stride_scale: float = 1.80
    rear_lift_scale: float = 0.75
    rear_phase_offset: float = 0.15
    rear_kick_fraction: float = 0.30
    rear_push_m: float = 0.030
    rear_knee_limit_rad: float = 0.30
    startup_s: float = 1.0
    ramp_s: float = 1.0


@dataclass(frozen=True, slots=True)
class AdaptiveControlConfig:
    enabled: bool = True
    learning_rate: float = 0.45
    initial_lead_s: float = 0.04
    max_lead_s: float = 0.30
    feedback_gain: float = 0.25


@dataclass(frozen=True, slots=True)
class ImuConfig:
    sample_rate_hz: float = 100.0
    delay_s: float = 0.02
    orientation_noise_std_deg: float = 0.12
    gyro_noise_std_deg_s: float = 0.35
    roll_bias_deg: float = 0.10
    pitch_bias_deg: float = -0.10
    random_seed: int = 7


@dataclass(frozen=True, slots=True)
class AdaptiveAttitudeControlConfig:
    enabled: bool = True
    kp: float = 0.55
    kd_s: float = 0.045
    adaptation_rate: float = 0.10
    trim_leak_rate: float = 0.015
    adaptation_deadband_deg: float = 0.35
    max_trim_deg: float = 4.0
    max_slope_deg: float = 8.0
    max_foot_correction_m: float = 0.022


@dataclass(frozen=True, slots=True)
class SimulationConfig:
    actuators: dict[str, PneumaticActuatorConfig]
    gait: GaitConfig
    adaptive_control: AdaptiveControlConfig
    imu: ImuConfig
    adaptive_attitude_control: AdaptiveAttitudeControlConfig


def default_config_path() -> Path:
    return Path(__file__).resolve().parents[3] / "config" / "pneumatic_sim.json"


def load_simulation_config(path: str | Path | None = None) -> SimulationConfig:
    config_path = Path(path) if path is not None else default_config_path()
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    raw_actuators = raw.get("actuators", {})
    missing = sorted(set(JOINT_NAMES) - set(raw_actuators))
    extra = sorted(set(raw_actuators) - set(JOINT_NAMES))
    if missing or extra:
        raise ValueError(f"Actuator configuration mismatch: missing={missing}, extra={extra}")

    actuators = {
        name: PneumaticActuatorConfig(**raw_actuators[name]) for name in JOINT_NAMES
    }
    for name, actuator in actuators.items():
        if actuator.delay_s < 0 or actuator.time_constant_s <= 0:
            raise ValueError(f"{name}: delay must be >= 0 and time_constant must be > 0")
        if actuator.extend_speed_rad_s <= 0 or actuator.retract_speed_rad_s <= 0:
            raise ValueError(f"{name}: actuator speeds must be > 0")
        if actuator.max_torque_nm <= 0:
            raise ValueError(f"{name}: max_torque_nm must be > 0")

    return SimulationConfig(
        actuators=actuators,
        gait=GaitConfig(**raw.get("gait", {})),
        adaptive_control=AdaptiveControlConfig(**raw.get("adaptive_control", {})),
        imu=ImuConfig(**raw.get("imu", {})),
        adaptive_attitude_control=AdaptiveAttitudeControlConfig(
            **raw.get("adaptive_attitude_control", {})
        ),
    )
