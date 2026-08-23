"""Simulation runner connecting gait, adaptation, pneumatic plants, and MuJoCo."""

from __future__ import annotations

import csv
import time
from dataclasses import dataclass, replace
from math import degrees
from pathlib import Path
from typing import TextIO

import numpy as np
from numpy.typing import NDArray

from highend_server.simulation.actuator import (
    AdaptiveTrackingController,
    PneumaticActuator,
)
from highend_server.simulation.config import JOINT_NAMES, SimulationConfig
from highend_server.simulation.gait import RabbitBoundGait
from highend_server.simulation.imu_control import (
    AdaptiveImuGaitController,
    AttitudeCorrection,
    ImuSample,
    SimulatedImu,
    quaternion_to_roll_pitch,
)


def default_model_path() -> Path:
    return Path(__file__).resolve().parents[3] / "simulation" / "pql_a00.xml"


@dataclass(frozen=True, slots=True)
class SimulationResult:
    duration_s: float
    forward_distance_m: float
    lateral_drift_m: float
    final_base_height_m: float
    max_abs_roll_deg: float
    max_abs_pitch_deg: float
    mean_abs_roll_deg: float
    mean_abs_pitch_deg: float
    mean_tracking_error_deg: float
    fallen: bool
    learned_lead_s: dict[str, float]
    learned_roll_trim_deg: float
    learned_pitch_trim_deg: float


class PqlA00Simulation:
    """Executable PQL-A00 digital twin.

    MuJoCo is imported lazily so the hardware server continues to run without
    installing the optional simulation dependencies on the Raspberry Pi.
    """

    def __init__(
        self,
        config: SimulationConfig,
        *,
        model_path: str | Path | None = None,
        adaptive: bool | None = None,
        imu_control: bool | None = None,
    ) -> None:
        try:
            import mujoco
        except ImportError as exc:  # pragma: no cover - exercised on machines without the extra
            raise RuntimeError(
                "MuJoCo is not installed. Run: python -m pip install -e '.[simulation]'"
            ) from exc

        self.mujoco = mujoco
        self.model_path = Path(model_path) if model_path else default_model_path()
        self.model = mujoco.MjModel.from_xml_path(str(self.model_path))
        self.data = mujoco.MjData(self.model)
        self.gait = RabbitBoundGait(config.gait)

        adaptive_config = config.adaptive_control
        if adaptive is not None:
            adaptive_config = replace(adaptive_config, enabled=adaptive)
        self.plants = [PneumaticActuator(config.actuators[name]) for name in JOINT_NAMES]
        self.controllers = [AdaptiveTrackingController(adaptive_config) for _ in JOINT_NAMES]
        attitude_config = config.adaptive_attitude_control
        if imu_control is not None:
            attitude_config = replace(attitude_config, enabled=imu_control)
        self.imu = SimulatedImu(config.imu)
        self.attitude_controller = AdaptiveImuGaitController(attitude_config)
        self.joint_qpos = np.asarray(
            [self.model.jnt_qposadr[self._joint_id(name)] for name in JOINT_NAMES], dtype=int
        )
        self.joint_dof = np.asarray(
            [self.model.jnt_dofadr[self._joint_id(name)] for name in JOINT_NAMES], dtype=int
        )
        self.base_qpos = int(self.model.jnt_qposadr[self._joint_id("root")])
        self._next_control_s = 0.0
        self._command_targets = np.zeros(len(JOINT_NAMES))
        self._desired_targets = np.zeros(len(JOINT_NAMES))
        self._last_imu = ImuSample(0.0, 0.0, 0.0, 0.0, 0.0)
        self._attitude_correction = AttitudeCorrection(
            np.zeros(4), 0.0, 0.0, 0.0, 0.0
        )

    def _joint_id(self, name: str) -> int:
        joint_id = self.mujoco.mj_name2id(
            self.model, self.mujoco.mjtObj.mjOBJ_JOINT, name
        )
        if joint_id < 0:
            raise ValueError(f"Joint {name!r} is missing from {self.model_path}")
        return joint_id

    def _sensor_data(self, name: str) -> NDArray[np.float64]:
        sensor_id = self.mujoco.mj_name2id(
            self.model, self.mujoco.mjtObj.mjOBJ_SENSOR, name
        )
        if sensor_id < 0:
            raise ValueError(f"Sensor {name!r} is missing from {self.model_path}")
        address = int(self.model.sensor_adr[sensor_id])
        dimension = int(self.model.sensor_dim[sensor_id])
        return self.data.sensordata[address : address + dimension]

    def reset(self) -> None:
        self.mujoco.mj_resetData(self.model, self.data)
        self.mujoco.mj_forward(self.model, self.data)
        current = self.data.qpos[self.joint_qpos]
        for plant, angle in zip(self.plants, current, strict=True):
            plant.reset(float(angle))
        for controller in self.controllers:
            controller.reset()
        self.imu.reset()
        self.attitude_controller.reset()
        self._last_imu = ImuSample(0.0, 0.0, 0.0, 0.0, 0.0)
        self._attitude_correction = AttitudeCorrection(
            np.zeros(4), 0.0, 0.0, 0.0, 0.0
        )
        self._next_control_s = 0.0

    def _control_tick(self, control_dt: float) -> None:
        now_s = float(self.data.time)
        self._last_imu = self.imu.update(
            now_s,
            self._sensor_data("imu_quat"),
            self._sensor_data("imu_gyro"),
        )
        self._attitude_correction = self.attitude_controller.update(
            self._last_imu, control_dt
        )
        desired, desired_velocity = self.gait.targets(
            now_s, self._attitude_correction.foot_height_m
        )
        measured = self.data.qpos[self.joint_qpos]
        self._desired_targets = desired
        for index, (controller, plant) in enumerate(
            zip(self.controllers, self.plants, strict=True)
        ):
            command = controller.command(
                float(desired[index]),
                float(desired_velocity[index]),
                float(measured[index]),
                control_dt,
            )
            joint_range = self.model.jnt_range[self._joint_id(JOINT_NAMES[index])]
            command = float(np.clip(command, joint_range[0], joint_range[1]))
            self._command_targets[index] = command
            plant.command(command, now_s)

    def _physics_tick(self) -> None:
        now_s = float(self.data.time)
        dt = float(self.model.opt.timestep)
        if now_s + 1e-9 >= self._next_control_s:
            self._control_tick(0.01)
            self._next_control_s += 0.01

        angles = self.data.qpos[self.joint_qpos]
        velocities = self.data.qvel[self.joint_dof]
        for index, plant in enumerate(self.plants):
            self.data.ctrl[index] = plant.update(
                now_s, dt, float(angles[index]), float(velocities[index])
            )
        self.mujoco.mj_step(self.model, self.data)

    def run(
        self,
        duration_s: float,
        *,
        viewer: bool = False,
        log_path: str | Path | None = None,
        quiet: bool = False,
    ) -> SimulationResult:
        if duration_s <= 0:
            raise ValueError("duration_s must be > 0")
        self.reset()
        initial_position = self.data.qpos[self.base_qpos : self.base_qpos + 3].copy()
        max_roll = 0.0
        max_pitch = 0.0
        attitude_error_sum = np.zeros(2)
        tracking_error_sum = 0.0
        tracking_samples = 0
        next_report_s = 1.0

        log_handle: TextIO | None = None
        log_writer: csv.writer | None = None
        if log_path is not None:
            output_path = Path(log_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            log_handle = output_path.open("w", newline="", encoding="utf-8")
            log_writer = csv.writer(log_handle)
            log_writer.writerow(
                [
                    "time_s",
                    "base_x_m",
                    "base_y_m",
                    "base_z_m",
                    "true_roll_deg",
                    "true_pitch_deg",
                    "imu_roll_deg",
                    "imu_pitch_deg",
                    "roll_trim_deg",
                    "pitch_trim_deg",
                ]
                + [f"foot_height_correction_m_{index}" for index in range(4)]
                + [f"desired_{name}" for name in JOINT_NAMES]
                + [f"actual_{name}" for name in JOINT_NAMES]
                + [f"lead_s_{name}" for name in JOINT_NAMES]
            )

        viewer_context = self._viewer_context() if viewer else _NullViewer()
        wall_start = time.perf_counter()
        try:
            with viewer_context as active_viewer:
                while self.data.time < duration_s and active_viewer.is_running():
                    self._physics_tick()
                    position = self.data.qpos[self.base_qpos : self.base_qpos + 3]
                    quaternion = self.data.qpos[self.base_qpos + 3 : self.base_qpos + 7]
                    roll_rad, pitch_rad = quaternion_to_roll_pitch(quaternion)
                    roll, pitch = degrees(roll_rad), degrees(pitch_rad)
                    max_roll = max(max_roll, abs(roll))
                    max_pitch = max(max_pitch, abs(pitch))
                    attitude_error_sum += np.abs((roll, pitch))
                    actual = self.data.qpos[self.joint_qpos]
                    tracking_error_sum += float(np.mean(np.abs(self._desired_targets - actual)))
                    tracking_samples += 1

                    if log_writer is not None:
                        log_writer.writerow(
                            [
                                self.data.time,
                                *position,
                                roll,
                                pitch,
                                degrees(self._last_imu.roll_rad),
                                degrees(self._last_imu.pitch_rad),
                                degrees(self._attitude_correction.roll_trim_rad),
                                degrees(self._attitude_correction.pitch_trim_rad),
                            ]
                            + self._attitude_correction.foot_height_m.tolist()
                            + self._desired_targets.tolist()
                            + actual.tolist()
                            + [controller.lead_s for controller in self.controllers]
                        )

                    if not quiet and self.data.time >= next_report_s:
                        forward = -(float(position[1]) - float(initial_position[1]))
                        print(
                            f"t={self.data.time:5.1f}s  forward={forward:+.3f}m  "
                            f"height={position[2]:.3f}m  roll={roll:+.1f}deg  pitch={pitch:+.1f}deg"
                        )
                        next_report_s += 1.0

                    active_viewer.sync()
                    if viewer:
                        target_wall_time = wall_start + float(self.data.time)
                        remaining = target_wall_time - time.perf_counter()
                        if remaining > 0:
                            time.sleep(remaining)
        finally:
            if log_handle is not None:
                log_handle.close()

        final_position = self.data.qpos[self.base_qpos : self.base_qpos + 3]
        mean_error_rad = tracking_error_sum / max(tracking_samples, 1)
        mean_attitude = attitude_error_sum / max(tracking_samples, 1)
        fallen = bool(final_position[2] < 0.16 or max_roll > 50.0 or max_pitch > 50.0)
        roll_trim_deg, pitch_trim_deg = self.attitude_controller.trim_degrees()
        return SimulationResult(
            duration_s=float(self.data.time),
            forward_distance_m=-(float(final_position[1]) - float(initial_position[1])),
            lateral_drift_m=float(final_position[0]) - float(initial_position[0]),
            final_base_height_m=float(final_position[2]),
            max_abs_roll_deg=max_roll,
            max_abs_pitch_deg=max_pitch,
            mean_abs_roll_deg=float(mean_attitude[0]),
            mean_abs_pitch_deg=float(mean_attitude[1]),
            mean_tracking_error_deg=degrees(mean_error_rad),
            fallen=fallen,
            learned_lead_s={
                name: float(controller.lead_s or 0.0)
                for name, controller in zip(JOINT_NAMES, self.controllers, strict=True)
            },
            learned_roll_trim_deg=roll_trim_deg,
            learned_pitch_trim_deg=pitch_trim_deg,
        )

    def _viewer_context(self):
        import mujoco.viewer

        handle = mujoco.viewer.launch_passive(self.model, self.data, show_left_ui=False)
        base_id = self.mujoco.mj_name2id(
            self.model, self.mujoco.mjtObj.mjOBJ_BODY, "base"
        )
        handle.cam.type = self.mujoco.mjtCamera.mjCAMERA_TRACKING
        handle.cam.trackbodyid = base_id
        handle.cam.distance = 1.4
        handle.cam.azimuth = 135
        handle.cam.elevation = -18
        return handle


class _NullViewer:
    def __enter__(self) -> _NullViewer:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def is_running(self) -> bool:
        return True

    def sync(self) -> None:
        return None
