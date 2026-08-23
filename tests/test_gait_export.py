"""Tests for the gait_lab waveform export (rad -> 0-4095 CSV)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")  # simulation extra; not a base dependency

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import gait_lab  # noqa: E402

from highend_server.application.control_service import ControlService  # noqa: E402
from highend_server.application.pql_a00_kinematics import (  # noqa: E402
    ACTUATOR_HEIGHT_EFFECTS,
)
from highend_server.config import Settings  # noqa: E402
from highend_server.domain.models import MotionCategory  # noqa: E402
from highend_server.simulation.config import GaitConfig  # noqa: E402
from highend_server.simulation.gait import PhaseOffsetGait  # noqa: E402
from highend_server.transport.serial_gateway import StubSerialGateway  # noqa: E402


def test_export_gains_match_hardware_foot_sign() -> None:
    # Moving a target in the +gain direction must raise the sim foot exactly
    # when the hardware model says +target raises the foot.
    for axis in range(8):
        gain = gait_lab.EXPORT_GAINS[axis]
        hw_sign = ACTUATOR_HEIGHT_EFFECTS[axis].height_sign
        sim_sign = gait_lab._sim_foot_z_sign(axis)
        # d foot_z_hw / d angle = hw_sign * gain must match the sim's sign.
        assert np.sign(hw_sign * gain) == sim_sign


def test_to_units_neutral_and_clamp() -> None:
    zeros = np.zeros((3, 8))
    units = gait_lab.to_units(zeros)
    assert (units == 2048).all() or (units == 2047).all()
    huge = np.full((2, 8), 10.0)
    clamped = gait_lab.to_units(huge)
    assert clamped.min() >= 0 and clamped.max() <= 4095


def test_feasibility_flags_fast_waveform() -> None:
    frames = np.tile(np.full(8, 2048), (10, 1))
    ratio, _ = gait_lab.feasibility(frames)
    assert ratio == 0.0

    # 800 units in one 0.04 s frame = 20 000 u/s: far beyond any axis.
    fast = frames.copy()
    fast[5, :] += 800
    ratio, _ = gait_lab.feasibility(fast)
    assert ratio > 1.0


def test_feasibility_includes_cycle_wraparound() -> None:
    frames = np.tile(np.full(8, 2048), (10, 1))
    frames[-1, 0] = 3000  # jump back to 2048 happens across the wrap
    ratio, axis = gait_lab.feasibility(frames)
    assert axis == 0
    assert ratio > 1.0


def test_exported_csv_parses_and_round_trips(tmp_path: Path) -> None:
    config = GaitConfig(cycle_s=8.0, duty_factor=0.7, stride_m=0.02, lift_m=0.015)
    gait = PhaseOffsetGait(config, (0.0, 0.5, 0.25, 0.75))
    angle_frames = gait_lab.sample_waveform(gait, config.cycle_s)
    unit_frames = gait_lab.to_units(angle_frames)
    candidate = gait_lab.Candidate(
        pattern="testgait",
        cycle_s=config.cycle_s,
        duty=config.duty_factor,
        stride_m=config.stride_m,
        lift_m=config.lift_m,
        phase_offsets=(0.0, 0.5, 0.25, 0.75),
    )

    class _Result:
        mean_abs_pitch_deg = 1.23

    out_dir = tmp_path / "Motion" / "Fixed Motion"
    out_dir.mkdir(parents=True)
    path = out_dir / "walk_testgait.csv"
    gait_lab.export_csv(candidate, unit_frames, _Result(), 0.01, path)

    # The server's parser must accept the file.
    settings = Settings(emulate_devices=True, motion_root_dir=str(tmp_path / "Motion"))

    async def sink(_event) -> None:
        return None

    control = ControlService(
        settings=settings, gateway=StubSerialGateway(settings), event_sink=sink
    )
    detail = control.get_motion_file(MotionCategory.FIXED, "walk_testgait")
    assert detail.item.interval_sec == pytest.approx(0.04)
    assert detail.item.loop is True
    assert detail.item.frame_count == unit_frames.shape[0]
    assert all(len(row) == 8 for row in detail.rows)

    # Phase advance is a circular shift: same multiset of values per column.
    parsed = np.array([[int(v) for v in row] for row in detail.rows])
    advances = gait_lab.phase_advance_frames()
    for axis in range(8):
        expected = np.roll(unit_frames[:, axis], -advances[axis])
        assert (parsed[:, axis] == expected).all()


def test_phase_offset_gait_shifts_leg_phase() -> None:
    config = GaitConfig(cycle_s=4.0, duty_factor=0.7, startup_s=0.0, ramp_s=0.01)
    base = PhaseOffsetGait(config, (0.0, 0.0, 0.0, 0.0))
    shifted = PhaseOffsetGait(config, (0.5, 0.0, 0.0, 0.0))
    t = 3.0
    same = base.foot_offset(1, t)
    moved = shifted.foot_offset(0, t)
    reference = base.foot_offset(0, t + 0.5 * config.cycle_s)
    assert np.allclose(moved, reference, atol=1e-9)
    assert np.allclose(same, base.foot_offset(1, t))
