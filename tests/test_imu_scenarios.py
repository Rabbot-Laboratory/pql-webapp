from __future__ import annotations

from math import isclose, isnan

import pytest

from highend_server.sensors.imu_scenarios import SCENARIOS, ScenarioSample, get_scenario


def test_get_scenario_unknown_name_raises_value_error_listing_names() -> None:
    with pytest.raises(ValueError) as exc_info:
        get_scenario("does-not-exist")
    message = str(exc_info.value)
    assert "does-not-exist" in message
    for name in SCENARIOS:
        assert name in message


def test_get_scenario_returns_registered_callables() -> None:
    for name, fn in SCENARIOS.items():
        assert get_scenario(name) is fn


def test_smooth_matches_legacy_formula_at_sample_points() -> None:
    from math import sin

    fn = get_scenario("smooth")
    for t in (0.0, 0.5, 1.3, 7.0):
        sample = fn(t)
        assert isclose(sample.roll_deg, 12.0 * sin(t * 0.85))
        assert isclose(sample.pitch_deg, 8.0 * sin(t * 0.57 + 0.9))
        assert isclose(sample.yaw_deg, t * 20.0)
        assert sample.accel_extra_g == ScenarioSample(0, 0, 0).accel_extra_g
        assert not sample.inject_nan
        assert not sample.hold_stale


def test_static_scenario_is_always_zero() -> None:
    fn = get_scenario("static")
    for t in (0.0, 3.0, 100.0):
        sample = fn(t)
        assert sample.roll_deg == 0.0
        assert sample.pitch_deg == 0.0
        assert sample.yaw_deg == 0.0


def test_roll_step_timing() -> None:
    fn = get_scenario("roll-step")
    assert fn(1.0).roll_deg == 0.0
    assert fn(3.0).roll_deg == pytest.approx(10.0)
    assert fn(6.0).roll_deg == 0.0
    # pitch/yaw untouched
    assert fn(3.0).pitch_deg == 0.0
    assert fn(3.0).yaw_deg == 0.0


def test_pitch_step_timing() -> None:
    fn = get_scenario("pitch-step")
    assert fn(1.0).pitch_deg == 0.0
    assert fn(3.0).pitch_deg == pytest.approx(8.0)
    assert fn(6.0).pitch_deg == 0.0
    assert fn(3.0).roll_deg == 0.0


def test_diagonal_step_timing() -> None:
    fn = get_scenario("diagonal-step")
    at_3 = fn(3.0)
    assert at_3.roll_deg == pytest.approx(8.0)
    assert at_3.pitch_deg == pytest.approx(6.0)
    assert fn(1.0).roll_deg == 0.0
    assert fn(1.0).pitch_deg == 0.0
    assert fn(6.0).roll_deg == 0.0
    assert fn(6.0).pitch_deg == 0.0


def test_impulse_peak_and_decay() -> None:
    fn = get_scenario("impulse")
    assert fn(2.9).roll_deg == 0.0
    peak = fn(3.0).roll_deg
    assert peak == pytest.approx(15.0)
    later = fn(3.25).roll_deg  # tau = 0.25s -> ~e^-1 of peak
    assert later < peak
    assert later == pytest.approx(15.0 * pow(2.718281828, -1.0), rel=1e-2)
    # decays toward (but need not reach) zero well after the pulse
    assert fn(5.0).roll_deg < 1.0


def test_oscillation_frequency_via_zero_crossings() -> None:
    fn = get_scenario("oscillation")
    # 0.5 Hz -> period 2s -> zero crossings at t=0,1,2,3...
    for t in (0.0, 1.0, 2.0, 3.0, 4.0):
        assert fn(t).roll_deg == pytest.approx(0.0, abs=1e-9)
    # quarter period (t=0.5) should be at the positive peak (+10 deg)
    assert fn(0.5).roll_deg == pytest.approx(10.0)
    assert fn(1.5).roll_deg == pytest.approx(-10.0)
    assert fn(0.0).pitch_deg == 0.0


def test_gyro_bias_constant_offset() -> None:
    fn = get_scenario("gyro-bias")
    for t in (0.0, 2.0, 10.0):
        sample = fn(t)
        assert sample.roll_deg == 0.0
        assert sample.pitch_deg == 0.0
        assert sample.gyro_bias_dps.x == pytest.approx(1.5)
        assert sample.gyro_bias_dps.y == pytest.approx(-0.8)
        assert sample.gyro_bias_dps.z == pytest.approx(0.5)


def test_accel_disturbance_leaves_attitude_level_but_adds_forward_pulse() -> None:
    fn = get_scenario("accel-disturbance")
    assert fn(0.0).roll_deg == 0.0
    assert fn(0.0).pitch_deg == 0.0
    assert fn(0.0).accel_extra_g.x == 0.0

    during = fn(3.5)
    assert during.roll_deg == 0.0
    assert during.pitch_deg == 0.0
    assert during.accel_extra_g.x == pytest.approx(0.35)

    after = fn(5.0)
    assert after.accel_extra_g.x == 0.0


def test_sensor_stale_flags_after_five_seconds() -> None:
    fn = get_scenario("sensor-stale")
    assert fn(1.0).hold_stale is False
    assert fn(4.999).hold_stale is False
    assert fn(5.0).hold_stale is True
    assert fn(20.0).hold_stale is True


def test_sensor_nan_flags_after_five_seconds() -> None:
    fn = get_scenario("sensor-nan")
    assert fn(1.0).inject_nan is False
    assert fn(4.999).inject_nan is False
    assert fn(5.0).inject_nan is True
    assert fn(20.0).inject_nan is True
    # attitude values themselves are never NaN — only the flag is set; the
    # fault is injected downstream in EmulatedImuSource.read().
    assert not isnan(fn(20.0).roll_deg)
