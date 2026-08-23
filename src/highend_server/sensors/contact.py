"""Server-side foot-contact detection from MCP3208 readings.

Pure hysteresis + debounce logic with no I/O or asyncio dependency so the
exact detector used on hardware can be unit-tested and replayed offline.
"""

from __future__ import annotations

from highend_server.domain.models import (
    AdcBankState,
    ContactCalibration,
    ContactLegCalibration,
    ContactLegState,
    ContactPolarity,
    SensorConnectionState,
)


class ContactDetector:
    """Stateful per-leg contact detector.

    ``update`` must be called exactly once per ADC poll tick: the debounce
    counters assume one call per sample.  A leg reports ``supporting`` only
    after ``debounce_ticks`` consecutive samples on the new side of its
    hysteresis band; missing samples (bank error / channel absent) reset the
    pending transition and keep the previous stable state with ``raw=None``.
    """

    def __init__(self, calibration: ContactCalibration) -> None:
        self._calibration = calibration
        self._supporting: dict[str, bool] = {item.leg: False for item in calibration.legs}
        self._pending_ticks: dict[str, int] = {item.leg: 0 for item in calibration.legs}

    @property
    def calibration(self) -> ContactCalibration:
        return self._calibration

    def update(self, adc_banks: list[AdcBankState]) -> list[ContactLegState]:
        bank = next(
            (item for item in adc_banks if item.device == self._calibration.device),
            None,
        )
        connected = bank is not None and bank.connection_state is SensorConnectionState.CONNECTED
        states: list[ContactLegState] = []
        for leg_cal in self._calibration.legs:
            raw: int | None = None
            voltage: float | None = None
            if connected and bank is not None:
                channel = next(
                    (item for item in bank.channels if item.channel == leg_cal.channel),
                    None,
                )
                if channel is not None:
                    raw = channel.raw
                    voltage = channel.voltage
            states.append(self._update_leg(leg_cal, raw, voltage))
        return states

    def _update_leg(
        self, leg_cal: ContactLegCalibration, raw: int | None, voltage: float | None
    ) -> ContactLegState:
        leg = leg_cal.leg
        if raw is None:
            self._pending_ticks[leg] = 0
            return ContactLegState(leg=leg, supporting=self._supporting[leg])

        current = self._supporting[leg]
        if leg_cal.polarity is ContactPolarity.ACTIVE_HIGH:
            desired = raw >= (leg_cal.off_threshold if current else leg_cal.on_threshold)
        else:
            desired = raw <= (leg_cal.off_threshold if current else leg_cal.on_threshold)

        if desired == current:
            self._pending_ticks[leg] = 0
        else:
            self._pending_ticks[leg] += 1
            if self._pending_ticks[leg] >= self._calibration.debounce_ticks:
                self._supporting[leg] = desired
                self._pending_ticks[leg] = 0

        return ContactLegState(
            leg=leg, raw=raw, voltage=voltage, supporting=self._supporting[leg]
        )
