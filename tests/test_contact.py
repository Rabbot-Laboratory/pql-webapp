"""Unit tests for the server-side foot-contact detector."""

from __future__ import annotations

import pytest

from highend_server.domain.models import (
    AdcBankState,
    AdcChannelState,
    ContactCalibration,
    ContactLegCalibration,
    ContactPolarity,
    LegId,
    SensorConnectionState,
)
from highend_server.sensors.contact import ContactDetector


def _bank(
    values: dict[int, int | None], *, device: int = 0, connected: bool = True
) -> AdcBankState:
    return AdcBankState(
        bus=0,
        device=device,
        connection_state=(
            SensorConnectionState.CONNECTED if connected else SensorConnectionState.ERROR
        ),
        channels=[
            AdcChannelState(bank=0, channel=channel, raw=raw, voltage=None)
            for channel, raw in values.items()
        ],
    )


def _calibration(**overrides) -> ContactCalibration:
    legs = [
        ContactLegCalibration(
            leg=leg,
            channel=channel,
            on_threshold=overrides.get("on_threshold", 2048),
            off_threshold=overrides.get("off_threshold", 2048),
            polarity=overrides.get("polarity", ContactPolarity.ACTIVE_HIGH),
        )
        for channel, leg in enumerate(
            (LegId.FRONT_RIGHT, LegId.FRONT_LEFT, LegId.REAR_RIGHT, LegId.REAR_LEFT)
        )
    ]
    return ContactCalibration(debounce_ticks=overrides.get("debounce_ticks", 2), legs=legs)


def _supporting(detector: ContactDetector, values: dict[int, int | None]) -> dict[LegId, bool]:
    states = detector.update([_bank(values)])
    return {state.leg: state.supporting for state in states}


def test_debounce_requires_consecutive_ticks() -> None:
    detector = ContactDetector(_calibration(debounce_ticks=2))
    high = {0: 3000, 1: 100, 2: 100, 3: 100}
    assert _supporting(detector, high)[LegId.FRONT_RIGHT] is False  # 1st tick pending
    assert _supporting(detector, high)[LegId.FRONT_RIGHT] is True  # 2nd tick commits
    # A single low tick must not release contact with debounce_ticks=2.
    low = {0: 100, 1: 100, 2: 100, 3: 100}
    assert _supporting(detector, low)[LegId.FRONT_RIGHT] is True
    assert _supporting(detector, high)[LegId.FRONT_RIGHT] is True  # pending reset
    assert _supporting(detector, low)[LegId.FRONT_RIGHT] is True
    assert _supporting(detector, low)[LegId.FRONT_RIGHT] is False


def test_hysteresis_band_holds_state() -> None:
    detector = ContactDetector(
        _calibration(on_threshold=2200, off_threshold=1800, debounce_ticks=1)
    )
    values = {0: 2300, 1: 0, 2: 0, 3: 0}
    assert _supporting(detector, values)[LegId.FRONT_RIGHT] is True
    values[0] = 2000  # inside band: stays supporting
    assert _supporting(detector, values)[LegId.FRONT_RIGHT] is True
    values[0] = 1799  # below off threshold: releases
    assert _supporting(detector, values)[LegId.FRONT_RIGHT] is False
    values[0] = 2000  # inside band: stays released
    assert _supporting(detector, values)[LegId.FRONT_RIGHT] is False


def test_active_low_polarity() -> None:
    detector = ContactDetector(
        _calibration(polarity=ContactPolarity.ACTIVE_LOW, debounce_ticks=1)
    )
    values = {0: 100, 1: 4000, 2: 4000, 3: 4000}
    result = _supporting(detector, values)
    assert result[LegId.FRONT_RIGHT] is True
    assert result[LegId.FRONT_LEFT] is False


def test_missing_data_keeps_previous_state_and_resets_pending() -> None:
    detector = ContactDetector(_calibration(debounce_ticks=2))
    high = {0: 3000, 1: 3000, 2: 3000, 3: 3000}
    _supporting(detector, high)
    assert all(_supporting(detector, high).values())

    # Disconnected bank: raw is None, previous supporting state is kept.
    states = detector.update([_bank(high, connected=False)])
    assert all(state.supporting for state in states)
    assert all(state.raw is None for state in states)

    # A pending transition is cancelled by a data gap.
    low = {0: 100, 1: 100, 2: 100, 3: 100}
    _supporting(detector, low)  # 1st pending release tick
    detector.update([_bank(low, connected=False)])  # gap resets pending
    assert _supporting(detector, low)[LegId.FRONT_RIGHT] is True  # counts as 1st again
    assert _supporting(detector, low)[LegId.FRONT_RIGHT] is False


def test_wrong_device_is_ignored() -> None:
    detector = ContactDetector(_calibration(debounce_ticks=1))
    states = detector.update([_bank({0: 4000, 1: 4000, 2: 4000, 3: 4000}, device=1)])
    assert all(state.raw is None and state.supporting is False for state in states)


def test_calibration_rejects_inconsistent_thresholds() -> None:
    with pytest.raises(ValueError):
        ContactLegCalibration(
            leg=LegId.FRONT_RIGHT,
            channel=0,
            on_threshold=1000,
            off_threshold=2000,
            polarity=ContactPolarity.ACTIVE_HIGH,
        )


def test_calibration_rejects_missing_leg() -> None:
    with pytest.raises(ValueError):
        ContactCalibration(
            legs=[ContactLegCalibration(leg=LegId.FRONT_RIGHT, channel=0)] * 4
        )
