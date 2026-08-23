"""Experiment-logging subsystem.

An experiment is a per-run directory under ``Logs/experiments/<id>/`` holding:

* ``manifest.json``  — run metadata (git, gains, imu config, config snapshot),
  written atomically (tmp + ``os.replace``) at start and again at stop.
* ``telemetry.csv``  — long-format sample stream (one row per actuator per
  sample tick), 59 fixed columns, block-buffered (never line-buffered).
* ``events.jsonl``   — teed WebSocket / action events, one compact JSON line
  each, flushed per write.
* ``notes.md``       — free-form operator notes appended live.

The recorder is late-bound (``bind``) because ``main.py`` constructs it before
the services it samples exist. A background sampler task paces on an absolute
deadline (no drift) at ``experiment_sample_rate_hz``.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.metadata
import json
import logging
import math
import os
import re
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING

from highend_server.application.attitude_control_frame import AttitudeControlFrame
from highend_server.config import Settings
from highend_server.domain.models import (
    ExperimentGitInfo,
    ExperimentManifest,
    ExperimentStartRequest,
    ExperimentStatus,
    ExperimentSummary,
    LegId,
    TelemetryEvent,
)

if TYPE_CHECKING:
    from highend_server.application.adaptive_walking import AdaptiveWalkingController
    from highend_server.application.control_service import ControlSample, ControlService
    from highend_server.application.stabilization import StabilizationController
    from highend_server.sensors.sensor_service import SensorService

logger = logging.getLogger(__name__)

# Exact experiment telemetry CSV header (order is load-bearing; the original
# 41 columns stay first so pre-existing analyzers keep working, walk/contact
# columns are appended after them).
CSV_HEADER: list[str] = [
    "timestamp",
    "elapsed_ms",
    "experiment_id",
    "git_sha",
    "motion_frame",
    "roll",
    "pitch",
    "level_roll",
    "level_pitch",
    "control_roll",
    "control_pitch",
    "yaw",
    "gyro_x",
    "gyro_y",
    "gyro_z",
    "accel_x",
    "accel_y",
    "accel_z",
    "accel_norm",
    "linear_accel_x",
    "linear_accel_y",
    "linear_accel_z",
    "mag_x",
    "mag_y",
    "mag_z",
    "mag_valid",
    "actuator_id",
    "actual_position",
    "base_target",
    "effective_target",
    "stabilization_correction",
    "pressure",
    "control_mode",
    "stabilization_enabled",
    "kp_roll",
    "ki_roll",
    "kd_roll",
    "kp_pitch",
    "ki_pitch",
    "kd_pitch",
    "accel_confidence_candidate",
    # -- adaptive walking (per tick; empty while not walking) ---------------
    "walk_active",
    "walk_phase",
    "walk_cycle",
    "walk_motion_scale",
    # -- foot contact (per tick; empty when no ADC data) --------------------
    "contact_fr_raw",
    "contact_fr",
    "contact_fl_raw",
    "contact_fl",
    "contact_rr_raw",
    "contact_rr",
    "contact_rl_raw",
    "contact_rl",
    # -- adaptive walking (per actuator row; empty while not walking) -------
    "walk_phase_offset",
    "walk_attitude_offset",
    "walk_phase_lead_s",
    "walk_rate_limited",
    "walk_saturated",
    "walk_ilc_correction",
]

# Column order of the contact_* CSV pairs.
_CONTACT_LEG_ORDER = (
    LegId.FRONT_RIGHT,
    LegId.FRONT_LEFT,
    LegId.REAR_RIGHT,
    LegId.REAR_LEFT,
)

# Event types teed from the WebSocket broadcast stream into events.jsonl.
_TEED_EVENT_TYPES = frozenset(
    {
        "csv_playback_status",
        "gamepad_state",
        "hardware_status",
        "playback_guard",
        "motion_request",
        "stabilization_state",
        "adaptive_walk_state",
        "adaptive_walk_gate",
        "adaptive_walk_ilc",
    }
)


class ExperimentAlreadyRunningError(RuntimeError):
    """Raised when start() is called while an experiment is already running."""


class ExperimentNotRunningError(RuntimeError):
    """Raised when stop()/add_note() is called while no experiment is running."""


def accel_confidence_candidate(norm_g: float) -> str:
    """Classify accelerometer-norm gravity confidence (record-only, pure).

    ``high`` when the norm is within 0.08 g of 1 g (near-static), ``medium``
    within 0.40 g, else ``low``. Recorded per row so downstream analysis can
    weight attitude by how gravity-dominated the accel reading was; the value
    is never fed back into control.
    """
    deviation = abs(norm_g - 1.0)
    if deviation <= 0.08:
        return "high"
    if deviation <= 0.40:
        return "medium"
    return "low"


def collect_git_info(project_root: Path) -> ExperimentGitInfo:
    """Best-effort git provenance (sha, branch, dirty). Never raises.

    Each git invocation is capped at a 2 s timeout; any failure (not a repo,
    git missing, timeout) degrades gracefully to the ``unknown`` defaults.
    """

    def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )

    try:
        sha = _run(["rev-parse", "HEAD"])
        branch = _run(["rev-parse", "--abbrev-ref", "HEAD"])
        status = _run(["status", "--porcelain"])
    except Exception:
        logger.debug("git info collection failed", exc_info=True)
        return ExperimentGitInfo()

    if sha.returncode != 0:
        return ExperimentGitInfo()
    return ExperimentGitInfo(
        sha=sha.stdout.strip() or "unknown",
        branch=(branch.stdout.strip() or "unknown") if branch.returncode == 0 else "unknown",
        dirty=bool(status.stdout.strip()) if status.returncode == 0 else None,
    )


def _package_version() -> str:
    try:
        return importlib.metadata.version("highend-control-server")
    except Exception:
        return "unknown"


def _sanitize_experiment_type(name: str) -> str:
    """Filesystem-safe experiment-type token for the run id.

    Mirrors ``ControlService._sanitize_motion_name`` (keep word chars, hyphen
    and space; replace everything else with ``_``) then collapses whitespace to
    ``_`` so the resulting directory id has no spaces.
    """
    sanitized = re.sub(r"[^\w\- ]+", "_", name.strip())
    sanitized = re.sub(r"\s+", "_", sanitized).strip("_.-")
    return sanitized or "experiment"


def _fmt_gain(value: float) -> str:
    return f"{value:g}"


class ExperimentRecorder:
    """Owns the lifecycle and IO of a single experiment run at a time."""

    def __init__(self, settings: Settings, *, time_fn: Callable[[], float] = monotonic) -> None:
        self._settings = settings
        self._time_fn = time_fn
        self._lock = asyncio.Lock()
        self._attitude_frame = AttitudeControlFrame(
            roll_sign=settings.stabilization_roll_sign,
            pitch_sign=settings.stabilization_pitch_sign,
        )

        # Late-bound collaborators (see bind()).
        self._control: ControlService | None = None
        self._stab: StabilizationController | None = None
        self._sensor: SensorService | None = None
        self._walk: AdaptiveWalkingController | None = None

        # Run state (only meaningful while _running is True).
        self._running = False
        self._experiment_id: str | None = None
        self._directory: Path | None = None
        self._manifest: ExperimentManifest | None = None
        self._git_sha = "unknown"
        self._started_at: datetime | None = None
        self._started_monotonic = 0.0
        self._telemetry_rows = 0
        self._event_count = 0
        self._sample_task: asyncio.Task[None] | None = None
        self._flush_deadline = 0.0
        self._last_stab_signature: tuple | None = None

        # Open file handles for the current run.
        self._csv_file = None
        self._events_file = None

    # -- binding -----------------------------------------------------------

    def bind(
        self,
        *,
        control_service: ControlService,
        stabilization_controller: StabilizationController,
        sensor_service: SensorService,
        adaptive_walking: AdaptiveWalkingController | None = None,
    ) -> None:
        self._control = control_service
        self._stab = stabilization_controller
        self._sensor = sensor_service
        self._walk = adaptive_walking

    # -- lifecycle ---------------------------------------------------------

    async def start(self, request: ExperimentStartRequest) -> ExperimentManifest:
        async with self._lock:
            if self._running:
                raise ExperimentAlreadyRunningError("an experiment is already running")
            if self._control is None or self._stab is None or self._sensor is None:
                raise RuntimeError("ExperimentRecorder.bind() has not been called")

            started_at = datetime.now(UTC)
            experiment_id = (
                started_at.strftime("%Y%m%d_%H%M%S")
                + "_"
                + _sanitize_experiment_type(request.experiment_type)
            )
            directory = self._settings.experiment_log_root_path / experiment_id
            directory.mkdir(parents=True, exist_ok=True)

            git = collect_git_info(self._settings.project_root)
            manifest = self._build_manifest(request, experiment_id, started_at, git)
            self._write_manifest_atomic(directory, manifest)

            # Default (block) buffering for the CSV: at ~200 rows/s a per-line
            # flush would hammer the SD card. Explicit flush cadence instead.
            self._csv_file = (directory / "telemetry.csv").open("w", newline="", encoding="utf-8")
            self._csv_file.write(",".join(CSV_HEADER) + "\r\n")
            self._events_file = (directory / "events.jsonl").open("a", encoding="utf-8")
            (directory / "notes.md").write_text(f"# Experiment {experiment_id} — notes\n", "utf-8")

            self._running = True
            self._experiment_id = experiment_id
            self._directory = directory
            self._manifest = manifest
            self._git_sha = git.sha
            self._started_at = started_at
            self._started_monotonic = self._time_fn()
            self._telemetry_rows = 0
            self._event_count = 0
            self._last_stab_signature = None
            self._flush_deadline = (
                self._started_monotonic + self._settings.experiment_flush_interval_sec
            )

            self._write_event(
                "experiment_start",
                {
                    "experiment_id": experiment_id,
                    "experiment_type": request.experiment_type,
                    "name": request.name,
                },
            )

            self._sample_task = asyncio.create_task(
                self._sample_loop(), name="experiment-sampler"
            )
            return manifest

    async def stop(self) -> ExperimentSummary:
        async with self._lock:
            if not self._running:
                raise ExperimentNotRunningError("no experiment is running")
            self._write_event("experiment_stop", {"experiment_id": self._experiment_id})
            self._running = False
            task = self._sample_task
            self._sample_task = None

        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        async with self._lock:
            return self._finalize_locked()

    async def shutdown(self) -> None:
        """Stop a running experiment if any; no-op when idle (lifespan finally)."""
        if self._running:
            with contextlib.suppress(ExperimentNotRunningError):
                await self.stop()

    # -- sampler -----------------------------------------------------------

    async def _sample_loop(self) -> None:
        period = 1.0 / self._settings.experiment_sample_rate_hz
        next_tick = self._time_fn()
        try:
            while True:
                next_tick += period
                await asyncio.sleep(max(0.0, next_tick - self._time_fn()))
                await self._sample_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("experiment sampler crashed; auto-stopping")
            with contextlib.suppress(Exception):
                self._write_event("recorder_error", {"error": "sampler crashed"})
            with contextlib.suppress(Exception):
                self._finalize_locked()

    async def _sample_once(self) -> int:
        if not self._running or self._csv_file is None:
            return 0
        assert self._control is not None
        attitude = self._sensor.latest_attitude() if self._sensor is not None else None
        stab_state = self._stab.get_state() if self._stab is not None else None
        ctrl: ControlSample = await self._control.sample_control_snapshot()

        now_mono = self._time_fn()
        elapsed_ms = (now_mono - self._started_monotonic) * 1000.0
        timestamp = datetime.now(UTC).isoformat(timespec="milliseconds")
        motion_frame = "" if ctrl.playback_row_index is None else str(ctrl.playback_row_index)

        if attitude is not None:
            accel = attitude.accel_g
            accel_norm = math.sqrt(accel.x**2 + accel.y**2 + accel.z**2)
            if self._sensor is not None:
                offset_roll, offset_pitch = self._sensor.level_offsets()
            else:
                offset_roll, offset_pitch = (0.0, 0.0)
            level_roll = attitude.euler.roll_deg - offset_roll
            level_pitch = attitude.euler.pitch_deg - offset_pitch
            control_roll, control_pitch = self._attitude_frame.tilt(
                raw_roll_deg=attitude.euler.roll_deg,
                raw_pitch_deg=attitude.euler.pitch_deg,
                level_roll_offset_deg=offset_roll,
                level_pitch_offset_deg=offset_pitch,
            )
            imu_cols = [
                f"{attitude.euler.roll_deg:.3f}",
                f"{attitude.euler.pitch_deg:.3f}",
                f"{level_roll:.3f}",
                f"{level_pitch:.3f}",
                f"{control_roll:.3f}",
                f"{control_pitch:.3f}",
                f"{attitude.euler.yaw_deg:.3f}",
                f"{attitude.gyro_dps.x:.3f}",
                f"{attitude.gyro_dps.y:.3f}",
                f"{attitude.gyro_dps.z:.3f}",
                f"{accel.x:.5f}",
                f"{accel.y:.5f}",
                f"{accel.z:.5f}",
                f"{accel_norm:.5f}",
                f"{attitude.linear_accel_g.x:.5f}",
                f"{attitude.linear_accel_g.y:.5f}",
                f"{attitude.linear_accel_g.z:.5f}",
                f"{attitude.mag.x:.5f}",
                f"{attitude.mag.y:.5f}",
                f"{attitude.mag.z:.5f}",
                "1" if attitude.mag_valid else "0",
            ]
            confidence = accel_confidence_candidate(accel_norm)
        else:
            imu_cols = [""] * 21
            confidence = ""

        walk = self._walk.latest_debug() if self._walk is not None else None
        if walk is not None and walk.active:
            walk_cols = [
                "1",
                f"{walk.phase:.4f}",
                str(walk.cycle_count),
                f"{walk.motion_scale:.3f}",
            ]
        else:
            walk_cols = ["0", "", "", ""]

        contact_cols = [""] * 8
        if self._sensor is not None:
            by_leg = {state.leg: state for state in self._sensor.latest_contact()}
            for slot, leg in enumerate(_CONTACT_LEG_ORDER):
                state = by_leg.get(leg)
                if state is not None and state.raw is not None:
                    contact_cols[slot * 2] = str(state.raw)
                    contact_cols[slot * 2 + 1] = "1" if state.supporting else "0"

        if stab_state is not None:
            stab_enabled = "1" if stab_state.enabled else "0"
            g = stab_state.gains
            gain_cols = [
                _fmt_gain(g.kp_roll),
                _fmt_gain(g.ki_roll),
                _fmt_gain(g.kd_roll),
                _fmt_gain(g.kp_pitch),
                _fmt_gain(g.ki_pitch),
                _fmt_gain(g.kd_pitch),
            ]
        else:
            stab_enabled = "0"
            gain_cols = [""] * 6

        prefix = [timestamp, f"{elapsed_ms:.1f}", self._experiment_id, self._git_sha, motion_frame]
        lines: list[str] = []
        for row in ctrl.rows:
            if walk is not None and walk.active and row.actuator_id < len(walk.phase_offsets):
                axis = row.actuator_id
                walk_axis_cols = [
                    f"{walk.phase_offsets[axis]:.2f}",
                    f"{walk.attitude_offsets[axis]:.2f}",
                    f"{walk.phase_leads_s[axis]:.4f}",
                    "1" if walk.rate_limited[axis] else "0",
                    "1" if walk.saturated[axis] else "0",
                    f"{walk.ilc_corrections[axis]:.2f}",
                ]
            else:
                walk_axis_cols = [""] * 6
            fields = [
                *prefix,
                *imu_cols,
                str(row.actuator_id),
                str(row.actual_position),
                str(row.base_target),
                str(row.effective_target),
                f"{row.correction:.2f}",
                str(row.pressure),
                row.control_mode,
                stab_enabled,
                *gain_cols,
                confidence,
                *walk_cols,
                *contact_cols,
                *walk_axis_cols,
            ]
            lines.append(",".join(fields))
        self._csv_file.write("\r\n".join(lines) + "\r\n")
        self._telemetry_rows += len(ctrl.rows)

        if now_mono >= self._flush_deadline:
            self._csv_file.flush()
            if self._events_file is not None:
                self._events_file.flush()
            self._flush_deadline = now_mono + self._settings.experiment_flush_interval_sec
        return len(ctrl.rows)

    # -- events ------------------------------------------------------------

    def observe_event(self, event: TelemetryEvent) -> None:
        """Tee a selected WebSocket event into events.jsonl. SYNC, no-op when idle."""
        if not self._running:
            return
        if event.type not in _TEED_EVENT_TYPES:
            return
        if event.type == "stabilization_state":
            signature = self._stab_signature(event.payload)
            if signature == self._last_stab_signature:
                return
            self._last_stab_signature = signature
        self._write_event(event.type, event.payload)

    def log_action(self, event_type: str, payload: dict) -> None:
        """Direct action hook (calibration, etc.). No-op when idle."""
        if not self._running:
            return
        self._write_event(event_type, payload)

    async def add_note(self, text: str) -> dict:
        async with self._lock:
            if not self._running or self._directory is None:
                raise ExperimentNotRunningError("no experiment is running")
            ts = datetime.now(UTC).isoformat(timespec="milliseconds")
            with (self._directory / "notes.md").open("a", encoding="utf-8") as handle:
                handle.write(f"- [{ts}] {text}\n")
            self._write_event("note", {"text": text})
            return {"experiment_id": self._experiment_id, "ts": ts, "text": text}

    # -- introspection -----------------------------------------------------

    def status(self) -> ExperimentStatus:
        if not self._running:
            return ExperimentStatus(running=False)
        return ExperimentStatus(
            running=True,
            experiment_id=self._experiment_id,
            directory=str(self._directory),
            elapsed_sec=self._time_fn() - self._started_monotonic,
            telemetry_rows=self._telemetry_rows,
            event_count=self._event_count,
        )

    def list_experiments(self) -> list[ExperimentManifest]:
        root = self._settings.experiment_log_root_path
        if not root.exists():
            return []
        manifests: list[ExperimentManifest] = []
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            manifest_path = child / "manifest.json"
            if not manifest_path.exists():
                continue
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifests.append(ExperimentManifest.model_validate(data))
            except Exception:
                logger.debug("skipping unparsable manifest %s", manifest_path, exc_info=True)
        manifests.sort(key=lambda m: m.started_at, reverse=True)
        return manifests

    def latest_experiment(self) -> ExperimentManifest | None:
        experiments = self.list_experiments()
        return experiments[0] if experiments else None

    # -- internals ---------------------------------------------------------

    def _build_manifest(
        self,
        request: ExperimentStartRequest,
        experiment_id: str,
        started_at: datetime,
        git: ExperimentGitInfo,
    ) -> ExperimentManifest:
        assert self._stab is not None and self._sensor is not None
        gains = self._stab.get_state().gains.model_dump()
        level_offsets = list(self._sensor.level_offsets())
        return ExperimentManifest(
            experiment_id=experiment_id,
            experiment_type=request.experiment_type,
            name=request.name,
            robot=self._settings.experiment_robot_name,
            package_version=_package_version(),
            git=git,
            started_at=started_at,
            sample_rate_hz=self._settings.experiment_sample_rate_hz,
            stabilization={
                "enabled": self._stab.enabled,
                "gains": gains,
                "derivative_source": self._settings.stabilization_derivative_source,
            },
            imu={
                "mahony_kp": self._settings.mahony_kp,
                "mahony_ki": self._settings.mahony_ki,
                "imu_sample_rate_hz": self._settings.imu_sample_rate_hz,
                "imu_mag_sample_rate_hz": self._settings.imu_mag_sample_rate_hz,
                "level_offsets": level_offsets,
            },
            config_snapshot=self._settings.model_dump(mode="json"),
        )

    def _write_manifest_atomic(self, directory: Path, manifest: ExperimentManifest) -> None:
        payload = json.dumps(manifest.model_dump(mode="json"), indent=2)
        tmp_path = directory / "manifest.json.tmp"
        tmp_path.write_text(payload, encoding="utf-8")
        os.replace(tmp_path, directory / "manifest.json")

    def _write_event(self, event_type: str, payload: dict) -> None:
        if self._events_file is None:
            return
        elapsed_ms = (self._time_fn() - self._started_monotonic) * 1000.0
        record = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "elapsed_ms": round(elapsed_ms, 1),
            "type": event_type,
            "payload": payload,
        }
        self._events_file.write(json.dumps(record, separators=(",", ":")) + "\n")
        self._events_file.flush()
        self._event_count += 1

    @staticmethod
    def _stab_signature(payload: dict) -> tuple:
        state = payload.get("stabilization", payload)
        gains = state.get("gains") or {}
        return (
            state.get("enabled"),
            state.get("active"),
            state.get("auto_disabled"),
            state.get("disabled_reason"),
            (
                gains.get("kp_roll"),
                gains.get("ki_roll"),
                gains.get("kd_roll"),
                gains.get("kp_pitch"),
                gains.get("ki_pitch"),
                gains.get("kd_pitch"),
            ),
        )

    def _finalize_locked(self) -> ExperimentSummary:
        assert self._directory is not None and self._manifest is not None
        ended_at = datetime.now(UTC)
        if self._csv_file is not None:
            with contextlib.suppress(Exception):
                self._csv_file.flush()
                self._csv_file.close()
            self._csv_file = None
        if self._events_file is not None:
            with contextlib.suppress(Exception):
                self._events_file.flush()
                self._events_file.close()
            self._events_file = None

        manifest = self._manifest.model_copy(
            update={
                "ended_at": ended_at,
                "row_counts": {
                    "telemetry_rows": self._telemetry_rows,
                    "events": self._event_count,
                },
            }
        )
        self._write_manifest_atomic(self._directory, manifest)
        self._manifest = manifest

        csv_path = self._directory / "telemetry.csv"
        telemetry_bytes = csv_path.stat().st_size if csv_path.exists() else 0
        summary = ExperimentSummary(
            manifest=manifest,
            directory=str(self._directory),
            duration_sec=self._time_fn() - self._started_monotonic,
            telemetry_rows=self._telemetry_rows,
            event_count=self._event_count,
            telemetry_bytes=telemetry_bytes,
        )
        self._running = False
        return summary
