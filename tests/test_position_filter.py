"""Tests for the potentiometer spike filter."""

from __future__ import annotations

from highend_server.application.position_filter import PositionSpikeFilter


def test_plausible_motion_passes_through() -> None:
    spike_filter = PositionSpikeFilter(2, max_speed_units_s=1500.0)
    assert spike_filter.filter(0, 2000, 0.0) == 2000
    # 1500 units/s * 0.04 s = 60 units allowed per tick.
    assert spike_filter.filter(0, 2050, 0.04) == 2050
    assert spike_filter.filter(0, 2010, 0.08) == 2010


def test_spike_is_rejected_and_previous_value_held() -> None:
    spike_filter = PositionSpikeFilter(2, max_speed_units_s=1500.0)
    spike_filter.filter(0, 2000, 0.0)
    # A 900-unit jump in 40 ms is ~22 500 units/s: no cylinder can do that.
    assert spike_filter.filter(0, 2900, 0.04) == 2000
    # A plausible reading right after is tracked again.
    assert spike_filter.filter(0, 2030, 0.08) == 2030


def test_persistent_new_reading_is_adopted() -> None:
    spike_filter = PositionSpikeFilter(2, max_speed_units_s=1500.0, reject_limit=3)
    spike_filter.filter(0, 2000, 0.0)
    held = [spike_filter.filter(0, 3500, 0.04 * (index + 1)) for index in range(4)]
    # Rejected while it looks like noise, then adopted so a genuine
    # recalibration or manual push is not latched out forever.
    assert held[:3] == [2000, 2000, 2000]
    assert held[3] == 3500


def test_axes_are_independent_and_reset_works() -> None:
    spike_filter = PositionSpikeFilter(2, max_speed_units_s=1500.0)
    spike_filter.filter(0, 2000, 0.0)
    spike_filter.filter(1, 100, 0.0)
    assert spike_filter.filter(1, 140, 0.04) == 140
    assert spike_filter.filter(0, 2900, 0.04) == 2000
    spike_filter.reset(0)
    assert spike_filter.filter(0, 2900, 0.08) == 2900


def test_zero_dt_holds_last_value() -> None:
    spike_filter = PositionSpikeFilter(1, max_speed_units_s=1500.0)
    spike_filter.filter(0, 2000, 1.0)
    assert spike_filter.filter(0, 3000, 1.0) == 2000
