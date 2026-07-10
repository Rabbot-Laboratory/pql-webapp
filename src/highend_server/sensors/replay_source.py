"""Replay an experiment's recorded telemetry.csv as an :class:`ImuSource`.

``ReplayImuSource`` lets the live Mahony filter (``ImuPipeline``) re-derive
attitude from a previously recorded run instead of real or emulated hardware,
which is invaluable for regression-testing filter/stabilization changes
against a known, repeatable motion trace.

Two pitfalls worth calling out up front (also documented in
``__main__.py --replay`` help text):

* **Double calibration.** The telemetry CSV stores *bias-corrected* gyro
  (``ImuPipeline`` already subtracted the gyro offset) and *calibrated* mag
  (hard/soft-iron corrected). If the replay server also loads a non-empty
  ``config/imu_calibration.json``, those corrections would be applied a
  SECOND time on top of already-corrected values. ``__main__.py --replay``
  guards against this by pointing ``HIGHEND_SENSOR_CONFIG_DIR_NAME`` at a
  fresh, empty temp directory unless the caller explicitly set it.
* **Shutdown responsiveness.** ``ReplayImuSource`` runs on the dedicated
  ``ImuPipeline`` thread and paces itself with a blocking ``sleep_fn`` between
  reads — exactly like a real I2C read would block. ``ImuPipeline.stop()``
  joins that thread with a bounded (default 2s) timeout, so a single very
  long blocking sleep (e.g. a large gap in the recorded ``elapsed_ms``
  sequence) could make shutdown appear to hang past that timeout (in
  practice `stop()` just gives up and logs, leaking the source rather than
  truly hanging — but that leak is exactly what we want to avoid). To keep
  worst-case per-call latency bounded, sleeps are issued in slices no longer
  than ``_MAX_SLEEP_SLICE_SEC`` rather than one long call. Note this bounds
  the size of each individual blocking call but does not, by itself, let an
  in-flight read() abort early mid-wait (there is no stop signal wired from
  ``ImuPipeline`` into ``ImuSource.read()``); in practice this is a
  non-issue for real recordings, since the CSV is sampled at
  ``experiment_sample_rate_hz`` (25 Hz default => ~40 ms gaps), far under the
  slice threshold.
"""

from __future__ import annotations

import csv
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

from highend_server.sensors.imu_bmx055 import Bmx055Reading, Vector3

logger = logging.getLogger(__name__)

_ZERO = Vector3(0.0, 0.0, 0.0)

# Columns that must be present in the telemetry CSV header for replay to work.
_REQUIRED_COLUMNS: tuple[str, ...] = (
    "elapsed_ms",
    "gyro_x",
    "gyro_y",
    "gyro_z",
    "accel_x",
    "accel_y",
    "accel_z",
    "mag_x",
    "mag_y",
    "mag_z",
    "mag_valid",
)

# Columns whose blankness signals "attitude was None this tick" (see
# application/experiment.py: imu_cols is [""] * 17 whenever the sensor
# service had no fused attitude snapshot yet). mag_valid is deliberately
# excluded here: a legitimate "0" (or blank, on very old recordings) does
# not mean the tick itself lacked a fused attitude, only that the mag
# component of that cycle's fusion wasn't fresh.
_BLANK_CHECK_COLUMNS: tuple[str, ...] = (
    "gyro_x",
    "gyro_y",
    "gyro_z",
    "accel_x",
    "accel_y",
    "accel_z",
    "mag_x",
    "mag_y",
    "mag_z",
)

# Upper bound on a single blocking sleep_fn() call inside ReplayImuSource.read().
# See the module docstring ("Shutdown responsiveness") for the rationale.
_MAX_SLEEP_SLICE_SEC = 0.2


@dataclass(slots=True)
class ReplaySample:
    elapsed_ms: float
    reading: Bmx055Reading


def load_replay_samples(csv_path: Path) -> list[ReplaySample]:
    """Parse an experiment telemetry.csv into a de-duplicated IMU sample stream.

    The CSV is long-format: ``actuator_count`` (typically 8) rows share the
    same ``elapsed_ms`` / IMU columns per sample tick. This collapses each
    tick to a single :class:`ReplaySample`, keeping the first row seen for a
    given ``elapsed_ms`` and skipping ticks whose IMU columns are blank
    (the sensor service had no fused attitude yet when that tick was
    recorded).

    Raises ``ValueError`` if the file is missing, required columns are
    absent from the header, or no usable (non-blank) samples remain.
    """
    if not csv_path.exists():
        raise ValueError(f"replay telemetry CSV not found: {csv_path}")

    with csv_path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        missing = [col for col in _REQUIRED_COLUMNS if col not in fieldnames]
        if missing:
            raise ValueError(
                f"replay telemetry CSV {csv_path} is missing required columns: "
                f"{', '.join(missing)}"
            )

        samples: list[ReplaySample] = []
        last_elapsed_ms: float | None = None
        for row in reader:
            elapsed_raw = row.get("elapsed_ms")
            if elapsed_raw in (None, ""):
                continue
            elapsed_ms = float(elapsed_raw)

            if last_elapsed_ms is not None and elapsed_ms == last_elapsed_ms:
                continue  # dedupe: multiple actuator rows share one tick
            last_elapsed_ms = elapsed_ms

            if any(row.get(col) in (None, "") for col in _BLANK_CHECK_COLUMNS):
                continue  # attitude was None this tick — nothing to replay

            gyro = Vector3(
                x=float(row["gyro_x"]),
                y=float(row["gyro_y"]),
                z=float(row["gyro_z"]),
            )
            accel = Vector3(
                x=float(row["accel_x"]),
                y=float(row["accel_y"]),
                z=float(row["accel_z"]),
            )
            mag = Vector3(
                x=float(row["mag_x"]),
                y=float(row["mag_y"]),
                z=float(row["mag_z"]),
            )
            # mag_valid is record-only here: whatever its value, the mag_x/y/z
            # columns already hold whatever ImuPipeline last published (either
            # this cycle's fresh calibrated mag or the carried-over last valid
            # reading) — reconstruct that faithfully rather than re-deriving
            # validity ourselves.
            reading = Bmx055Reading(
                accel_g=accel,
                gyro_dps=gyro,
                mag_raw=mag,
                temperature_c=None,
            )
            samples.append(ReplaySample(elapsed_ms=elapsed_ms, reading=reading))

    if not samples:
        raise ValueError(f"replay telemetry CSV {csv_path} contains no usable IMU samples")

    return samples


class ReplayImuSource:
    """``ImuSource`` that replays a recorded experiment's telemetry.csv.

    Runs on the ``ImuPipeline`` thread exactly like ``RealImuSource``: each
    ``read()`` call blocks (via ``sleep_fn``) until the recorded sample's
    original wall-clock spacing has elapsed (scaled by ``time_scale``), then
    returns that sample's reading. This lets the live Mahony filter re-derive
    attitude with the exact same code path used on hardware.
    """

    def __init__(
        self,
        experiment_dir: Path,
        *,
        time_scale: float = 1.0,
        time_fn: Callable[[], float] = monotonic,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._experiment_dir = experiment_dir
        self._time_scale = time_scale
        self._time_fn = time_fn
        self._sleep_fn = sleep_fn

        self._samples: list[ReplaySample] = []
        self._index = 0
        self._start: float | None = None
        self._first_elapsed_ms = 0.0
        self._finished = False
        self._finished_logged = False

    def open(self) -> None:
        csv_path = self._experiment_dir / "telemetry.csv"
        samples = load_replay_samples(csv_path)

        self._samples = samples
        self._index = 0
        self._start = self._time_fn()
        self._first_elapsed_ms = samples[0].elapsed_ms
        self._finished = False
        self._finished_logged = False

        duration_sec = (samples[-1].elapsed_ms - self._first_elapsed_ms) / 1000.0
        logger.info(
            "ReplayImuSource: loaded %d samples from %s (%.2fs recorded, time_scale=%.3g)",
            len(samples),
            csv_path,
            duration_sec,
            self._time_scale,
        )

    def close(self) -> None:
        pass  # nothing to release: samples are held in memory only

    def read(self) -> Bmx055Reading:
        if self._start is None or not self._samples:
            raise RuntimeError("ReplayImuSource.read() called before open()")

        if self._index >= len(self._samples):
            if not self._finished_logged:
                logger.info(
                    "ReplayImuSource: replay finished after %d samples; freezing "
                    "last reading with zero gyro (attitude holds, no yaw drift)",
                    len(self._samples),
                )
                self._finished_logged = True
            self._finished = True
            last = self._samples[-1].reading
            return Bmx055Reading(
                accel_g=last.accel_g,
                gyro_dps=_ZERO,
                mag_raw=last.mag_raw,
                temperature_c=last.temperature_c,
            )

        sample = self._samples[self._index]
        due = (
            self._start
            + (sample.elapsed_ms - self._first_elapsed_ms) / 1000.0 / self._time_scale
        )
        self._sleep_until(due)
        self._index += 1
        return sample.reading

    def _sleep_until(self, due: float) -> None:
        """Block (via ``sleep_fn``) until ``due``, in bounded slices.

        Always issues at least one ``sleep_fn`` call (with 0 if already past
        due) so callers pacing off recorded sleep calls see an explicit
        zero rather than a silently skipped wait.
        """
        remaining = due - self._time_fn()
        if remaining <= 0:
            self._sleep_fn(0.0)
            return
        while remaining > 0:
            chunk = min(remaining, _MAX_SLEEP_SLICE_SEC)
            self._sleep_fn(chunk)
            remaining = due - self._time_fn()

    @property
    def finished(self) -> bool:
        return self._finished

    @property
    def sample_count(self) -> int:
        return len(self._samples)
