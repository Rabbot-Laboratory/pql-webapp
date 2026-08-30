"""Spike rejection for the actuator position feedback.

Measured on hardware (2026-08-30, run 20260830_090211_walk-tuning): the
reported position jumps by up to 937 units between consecutive 40 ms samples
*while the robot is standing still*, and up to 2902 units while walking. The
cylinders themselves top out around 344-819 units/s (full-stroke bang probe),
i.e. at most ~33 units per 40 ms tick, so those jumps are electrical/ADC
noise on the potentiometer line, not motion.

Unfiltered, every spike is fed to the phase-lead learner, the standing
overdrive and the stabilizer as if it were a real tracking error. This filter
rejects a sample that moved further than the axis physically can, and lets it
through once the axis stays there (so a genuine re-calibration, a manual push
or a captured-range change still converges instead of latching forever).
"""

from __future__ import annotations

from dataclasses import dataclass

# Fastest full-stroke speed measured on any axis (RL knee 819 units/s,
# 2026-08-30) with generous headroom for load transients and sampling jitter.
DEFAULT_MAX_SPEED_UNITS_S = 1500.0
# Consecutive rejected samples after which the new reading is accepted anyway.
DEFAULT_REJECT_LIMIT = 3


@dataclass(slots=True)
class _AxisState:
    value: float | None = None
    at: float | None = None
    rejected: int = 0


class PositionSpikeFilter:
    """Per-axis rate-of-change gate for reported positions."""

    def __init__(
        self,
        actuator_count: int,
        *,
        max_speed_units_s: float = DEFAULT_MAX_SPEED_UNITS_S,
        reject_limit: int = DEFAULT_REJECT_LIMIT,
    ) -> None:
        if max_speed_units_s <= 0:
            raise ValueError("max_speed_units_s must be > 0")
        if reject_limit < 1:
            raise ValueError("reject_limit must be >= 1")
        self._max_speed = max_speed_units_s
        self._reject_limit = reject_limit
        self._axes = [_AxisState() for _ in range(actuator_count)]

    def filter(self, index: int, position: int, now_s: float) -> int:
        """Return the position to use for ``index`` at time ``now_s``."""
        if not 0 <= index < len(self._axes):
            return position
        state = self._axes[index]
        if state.value is None or state.at is None:
            state.value, state.at, state.rejected = float(position), now_s, 0
            return position

        dt = now_s - state.at
        if dt <= 0.0:
            return int(round(state.value))
        allowed = self._max_speed * dt
        delta = float(position) - state.value
        if abs(delta) <= allowed or state.rejected >= self._reject_limit:
            state.value, state.at, state.rejected = float(position), now_s, 0
            return position

        # Implausible jump: hold the previous value, but keep counting so a
        # persistent new reading is adopted instead of being rejected forever.
        state.rejected += 1
        state.at = now_s
        return int(round(state.value))

    def reset(self, index: int | None = None) -> None:
        targets = self._axes if index is None else [self._axes[index]]
        for state in targets:
            state.value = None
            state.at = None
            state.rejected = 0
