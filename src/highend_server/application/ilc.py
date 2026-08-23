"""Iterative learning control for the periodic walking trajectory.

Pure functions with no asyncio/serial/sensor dependency (same isolation
policy as ``compute_adaptive_walking_targets``): a per-frame, per-axis
feed-forward correction table is updated once per gait cycle from that
cycle's mean tracking errors.

Acceptance rule: an update is kept only while the cycle RMS error is not
getting worse. When a cycle regresses, the table reverts to its state before
the previous update and the RMS baseline restarts, so a bad update can never
compound across cycles.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import sqrt

Table = tuple[tuple[float, ...], ...]


@dataclass(frozen=True, slots=True)
class IlcState:
    table: Table  # frames x axes, added to the nominal trajectory
    previous_table: Table  # table before the last accepted update
    last_cycle_rms: float | None = None


@dataclass(frozen=True, slots=True)
class IlcUpdateResult:
    state: IlcState
    accepted: bool
    cycle_rms: float


def make_ilc_state(frame_count: int, axis_count: int) -> IlcState:
    zeros: Table = tuple((0.0,) * axis_count for _ in range(frame_count))
    return IlcState(table=zeros, previous_table=zeros)


def update_ilc(
    state: IlcState,
    cycle_errors: Sequence[Sequence[float | None]],
    *,
    gain: float,
    max_correction: float,
) -> IlcUpdateResult:
    """Fold one completed cycle's mean per-frame errors into the table.

    ``cycle_errors[frame][axis]`` is the mean of ``nominal - actual`` over the
    samples that landed in that frame, or ``None`` when the frame received no
    samples (its correction is carried forward unchanged).
    """
    frames = len(state.table)
    if len(cycle_errors) != frames:
        raise ValueError("cycle_errors must match the table frame count")

    flat = [
        value
        for frame in cycle_errors
        for value in frame
        if value is not None
    ]
    cycle_rms = sqrt(sum(value * value for value in flat) / len(flat)) if flat else 0.0

    if state.last_cycle_rms is not None and cycle_rms > state.last_cycle_rms:
        # The previous update made tracking worse: revert it and restart the
        # baseline so the reverted table gets a fresh comparison cycle.
        return IlcUpdateResult(
            state=IlcState(
                table=state.previous_table,
                previous_table=state.previous_table,
                last_cycle_rms=None,
            ),
            accepted=False,
            cycle_rms=cycle_rms,
        )

    updated = [
        [
            state.table[frame][axis]
            + gain * (error if (error := cycle_errors[frame][axis]) is not None else 0.0)
            for axis in range(len(state.table[frame]))
        ]
        for frame in range(frames)
    ]
    smoothed = _smooth_frames(updated)
    clamped: Table = tuple(
        tuple(max(-max_correction, min(max_correction, value)) for value in frame)
        for frame in smoothed
    )
    return IlcUpdateResult(
        state=IlcState(
            table=clamped,
            previous_table=state.table,
            last_cycle_rms=cycle_rms,
        ),
        accepted=True,
        cycle_rms=cycle_rms,
    )


def sample_ilc(state: IlcState, frame_position: float) -> tuple[float, ...]:
    """Linearly interpolate the correction table at a fractional frame index."""
    frames = len(state.table)
    if frames == 0:
        return ()
    index = int(frame_position) % frames
    next_index = (index + 1) % frames
    fraction = frame_position - int(frame_position)
    current = state.table[index]
    following = state.table[next_index]
    return tuple(
        current[axis] + (following[axis] - current[axis]) * fraction
        for axis in range(len(current))
    )


def _smooth_frames(table: list[list[float]]) -> list[list[float]]:
    """Circular 3-point [0.25, 0.5, 0.25] moving average along the frame axis.

    Keeps the feed-forward waveform from accumulating single-frame spikes that
    the pneumatic actuators cannot follow anyway.
    """
    frames = len(table)
    if frames < 3:
        return table
    return [
        [
            0.25 * table[(frame - 1) % frames][axis]
            + 0.5 * table[frame][axis]
            + 0.25 * table[(frame + 1) % frames][axis]
            for axis in range(len(table[frame]))
        ]
        for frame in range(frames)
    ]
