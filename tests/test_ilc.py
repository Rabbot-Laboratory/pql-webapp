"""Unit tests for the per-cycle iterative learning control table."""

from __future__ import annotations

import pytest

from highend_server.application.ilc import (
    make_ilc_state,
    sample_ilc,
    update_ilc,
)


def test_update_folds_errors_into_table() -> None:
    state = make_ilc_state(frame_count=4, axis_count=2)
    errors = [[10.0, -10.0]] * 4
    outcome = update_ilc(state, errors, gain=0.5, max_correction=100.0)
    assert outcome.accepted is True
    assert outcome.cycle_rms == pytest.approx(10.0)
    # Uniform errors survive the 3-point smoothing unchanged.
    assert outcome.state.table[0][0] == pytest.approx(5.0)
    assert outcome.state.table[0][1] == pytest.approx(-5.0)


def test_repeated_updates_converge_on_constant_error_plant() -> None:
    # Plant with a constant offset: actual = nominal - 20 + correction.
    state = make_ilc_state(frame_count=3, axis_count=1)
    for _ in range(20):
        correction = state.table[0][0]
        error = 20.0 - correction  # nominal - actual
        outcome = update_ilc(state, [[error]] * 3, gain=0.5, max_correction=100.0)
        state = outcome.state
    assert state.table[0][0] == pytest.approx(20.0, abs=0.5)


def test_correction_is_clamped() -> None:
    state = make_ilc_state(frame_count=2, axis_count=1)
    outcome = update_ilc(state, [[1000.0], [1000.0]], gain=1.0, max_correction=50.0)
    assert all(frame[0] == 50.0 for frame in outcome.state.table)


def test_regression_reverts_previous_update() -> None:
    state = make_ilc_state(frame_count=2, axis_count=1)
    first = update_ilc(state, [[10.0], [10.0]], gain=1.0, max_correction=100.0)
    assert first.accepted is True
    table_after_first = first.state.table

    # The next cycle got WORSE: the first update must be rolled back.
    second = update_ilc(first.state, [[50.0], [50.0]], gain=1.0, max_correction=100.0)
    assert second.accepted is False
    assert second.state.table == first.state.previous_table
    assert second.state.table != table_after_first
    # Baseline restarts so the reverted table gets a fresh comparison cycle.
    assert second.state.last_cycle_rms is None


def test_missing_frames_carry_previous_correction() -> None:
    state = make_ilc_state(frame_count=3, axis_count=1)
    seeded = update_ilc(state, [[10.0], [10.0], [10.0]], gain=1.0, max_correction=100.0)
    before = seeded.state.table[1][0]
    outcome = update_ilc(
        seeded.state, [[0.0], [None], [0.0]], gain=1.0, max_correction=100.0
    )
    # Frame 1 had no samples: only smoothing from neighbours may move it.
    assert outcome.state.table[1][0] == pytest.approx(before, abs=1e-9)


def test_sample_interpolates_between_frames() -> None:
    state = make_ilc_state(frame_count=2, axis_count=1)
    outcome = update_ilc(state, [[10.0], [30.0]], gain=1.0, max_correction=100.0)
    # Below 3 frames the smoothing pass is skipped: table stays [10, 30].
    assert sample_ilc(outcome.state, 0.0)[0] == pytest.approx(10.0)
    assert sample_ilc(outcome.state, 0.5)[0] == pytest.approx(20.0)
    assert sample_ilc(outcome.state, 1.5)[0] == pytest.approx(20.0)  # wraps


def test_frame_count_mismatch_raises() -> None:
    state = make_ilc_state(frame_count=3, axis_count=1)
    with pytest.raises(ValueError):
        update_ilc(state, [[1.0]], gain=1.0, max_correction=10.0)
