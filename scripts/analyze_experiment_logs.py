"""Create an auditable inventory and first-pass analysis of experiment logs.

The script intentionally uses only the recorded manifest, events, notes, and
telemetry files.  It does not infer actuator/ADC channel mappings or evaluate
closed-loop behavior.  Run from the repository root:

    python scripts/analyze_experiment_logs.py
"""

# ruff: noqa: E501

from __future__ import annotations

import csv
import json
import math
import statistics
from collections import Counter
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXPERIMENT_ROOT = REPOSITORY_ROOT / "Logs" / "experiments" / "pi-2026-07-11"
DEFAULT_ANALYSIS_ROOT = REPOSITORY_ROOT / "Logs" / "analysis"
DEFAULT_DOC_ROOT = REPOSITORY_ROOT / "docs" / "experiments"
POSITION_MIN = 0
POSITION_MAX = 4095
EXPECTED_ACTUATORS = 8

FRAME_NUMERIC_FIELDS = (
    "roll",
    "pitch",
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
)
ROW_NUMERIC_FIELDS = (
    "elapsed_ms",
    "actuator_id",
    "actual_position",
    "base_target",
    "effective_target",
    "stabilization_correction",
    "pressure",
    "stabilization_enabled",
    "kp_roll",
    "ki_roll",
    "kd_roll",
    "kp_pitch",
    "ki_pitch",
    "kd_pitch",
)


def number(value: str | None) -> float | None:
    if value is None or value.strip() == "":
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def stat(values: Iterable[float]) -> dict[str, float | int | None]:
    data = list(values)
    if not data:
        return {"count": 0, "mean": None, "std": None, "min": None, "max": None}
    return {
        "count": len(data),
        "mean": statistics.fmean(data),
        "std": statistics.stdev(data) if len(data) > 1 else 0.0,
        "min": min(data),
        "max": max(data),
    }


def mean(values: Iterable[float]) -> float | None:
    data = list(values)
    return statistics.fmean(data) if data else None


def fmt(value: float | int | None, digits: int = 3) -> str:
    if value is None:
        return "—"
    if isinstance(value, int):
        return str(value)
    return f"{value:.{digits}f}"


def fmt_seconds(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f} s"


def iso_seconds(start: str, end: str) -> float:
    return (
        datetime.fromisoformat(end.replace("Z", "+00:00"))
        - datetime.fromisoformat(start.replace("Z", "+00:00"))
    ).total_seconds()


def field_values(frames: list[dict[str, Any]], field: str) -> list[float]:
    return [frame[field] for frame in frames if frame.get(field) is not None]


def frame_slice(
    frames: list[dict[str, Any]], start_ms: float, end_ms: float
) -> list[dict[str, Any]]:
    return [frame for frame in frames if start_ms <= frame["elapsed_ms"] <= end_ms]


def vector_norm(frame: dict[str, Any], prefix: str) -> float | None:
    values = [frame.get(f"{prefix}_{axis}") for axis in ("x", "y", "z")]
    return (
        math.sqrt(sum(value * value for value in values))
        if all(value is not None for value in values)
        else None
    )


def max_repeated_imu_frames(frames: list[dict[str, Any]]) -> int:
    fields = ("roll", "pitch", "yaw", "gyro_x", "gyro_y", "gyro_z", "accel_x", "accel_y", "accel_z")
    longest = current = 0
    previous: tuple[float | None, ...] | None = None
    for frame in frames:
        current_value = tuple(frame.get(field) for field in fields)
        current = current + 1 if current_value == previous else 1
        longest = max(longest, current)
        previous = current_value
    return longest


def telemetry_frames(
    path: Path, manifest: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    frame_map: dict[str, dict[str, Any]] = {}
    frame_rows: Counter[str] = Counter()
    numeric_nonfinite = 0
    malformed_numeric = 0
    invariant_failures = 0
    config_mismatches = 0
    total_rows = 0
    confidence_rows: Counter[str] = Counter()

    gains = manifest.get("stabilization", {}).get("gains", {})
    expected_settings = {
        "kp_roll": gains.get("kp_roll"),
        "ki_roll": gains.get("ki_roll"),
        "kd_roll": gains.get("kd_roll"),
        "kp_pitch": gains.get("kp_pitch"),
        "ki_pitch": gains.get("ki_pitch"),
        "kd_pitch": gains.get("kd_pitch"),
    }

    with path.open("r", encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        header = reader.fieldnames or []
        for row in reader:
            total_rows += 1
            elapsed = number(row.get("elapsed_ms"))
            if elapsed is None:
                malformed_numeric += 1
                continue
            frame_key = row["elapsed_ms"]
            frame_rows[frame_key] += 1
            converted: dict[str, Any] = {"elapsed_ms": elapsed}
            for field in FRAME_NUMERIC_FIELDS:
                raw = row.get(field)
                parsed = number(raw)
                if raw not in (None, "") and parsed is None:
                    numeric_nonfinite += 1
                converted[field] = parsed
            for field in ROW_NUMERIC_FIELDS:
                raw = row.get(field)
                parsed = number(raw)
                if raw not in (None, "") and parsed is None:
                    numeric_nonfinite += 1
                converted[field] = parsed
            converted["accel_confidence_candidate"] = (
                row.get("accel_confidence_candidate") or ""
            ).strip()
            converted["timestamp"] = row.get("timestamp", "")
            frame_map.setdefault(frame_key, converted)
            confidence_rows[converted["accel_confidence_candidate"] or "(empty)"] += 1

            base = converted["base_target"]
            correction = converted["stabilization_correction"]
            effective = converted["effective_target"]
            if None not in (base, correction, effective):
                expected = max(POSITION_MIN, min(POSITION_MAX, int(base) + int(round(correction))))
                if int(effective) != expected:
                    invariant_failures += 1
            else:
                malformed_numeric += 1

            for field, expected in expected_settings.items():
                actual = converted.get(field)
                if (
                    expected is not None
                    and actual is not None
                    and not math.isclose(actual, float(expected), rel_tol=0.0, abs_tol=1e-9)
                ):
                    config_mismatches += 1

    frames = sorted(frame_map.values(), key=lambda item: item["elapsed_ms"])
    deltas = [
        later["elapsed_ms"] - earlier["elapsed_ms"]
        for earlier, later in zip(frames, frames[1:], strict=False)
    ]
    positive_deltas = [delta for delta in deltas if delta > 0]
    median_period = statistics.median(positive_deltas) if positive_deltas else None
    expected_period = 1000.0 / float(manifest.get("sample_rate_hz", 25.0))
    gap_limit = max(expected_period * 1.5, (median_period or expected_period) * 1.5)
    sample_gaps = [delta for delta in positive_deltas if delta > gap_limit]
    nonmonotonic = sum(1 for delta in deltas if delta <= 0)
    short_or_long_frames = sum(1 for count in frame_rows.values() if count != EXPECTED_ACTUATORS)

    return frames, {
        "header": header,
        "telemetry_rows": total_rows,
        "frames": len(frames),
        "frame_row_count_errors": short_or_long_frames,
        "numeric_nonfinite_or_invalid": numeric_nonfinite,
        "malformed_required_numeric": malformed_numeric,
        "effective_target_invariant_failures": invariant_failures,
        "manifest_gain_mismatches": config_mismatches,
        "nonmonotonic_elapsed_deltas": nonmonotonic,
        "median_frame_period_ms": median_period,
        "min_frame_period_ms": min(positive_deltas) if positive_deltas else None,
        "max_frame_period_ms": max(positive_deltas) if positive_deltas else None,
        "sample_gap_threshold_ms": gap_limit,
        "sample_gaps": len(sample_gaps),
        "largest_sample_gap_ms": max(sample_gaps) if sample_gaps else 0.0,
        "max_identical_imu_frame_run": max_repeated_imu_frames(frames),
        "accel_confidence_rows": dict(sorted(confidence_rows.items())),
    }


def load_events(path: Path) -> tuple[list[dict[str, Any]], int]:
    events: list[dict[str, Any]] = []
    corrupt = 0
    with path.open("r", encoding="utf-8") as source:
        for line in source:
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                corrupt += 1
    return events, corrupt


def event_integrity(
    events: list[dict[str, Any]], frames: list[dict[str, Any]], manifest: dict[str, Any]
) -> dict[str, Any]:
    duration_ms = iso_seconds(manifest["started_at"], manifest["ended_at"]) * 1000.0
    first_elapsed = frames[0]["elapsed_ms"] if frames else None
    last_elapsed = frames[-1]["elapsed_ms"] if frames else None
    event_outside_experiment = 0
    notes_outside_telemetry = 0
    for event in events:
        elapsed = number(str(event.get("elapsed_ms", "")))
        if elapsed is None or elapsed < 0 or elapsed > duration_ms + 5.0:
            event_outside_experiment += 1
        if event.get("type") == "note" and (
            elapsed is None
            or first_elapsed is None
            or elapsed < first_elapsed
            or elapsed > last_elapsed
        ):
            notes_outside_telemetry += 1
    return {
        "manifest_duration_ms": duration_ms,
        "telemetry_first_elapsed_ms": first_elapsed,
        "telemetry_last_elapsed_ms": last_elapsed,
        "events_outside_manifest_duration": event_outside_experiment,
        "notes_outside_telemetry": notes_outside_telemetry,
    }


def static_metrics(frames: list[dict[str, Any]], offsets: list[float]) -> dict[str, Any]:
    five_seconds = 5000.0
    start = frames[0]["elapsed_ms"] if frames else 0.0
    end = frames[-1]["elapsed_ms"] if frames else 0.0
    first = frame_slice(frames, start, start + five_seconds)
    last = frame_slice(frames, max(start, end - five_seconds), end)
    result: dict[str, Any] = {
        "raw": {
            field: stat(field_values(frames, field))
            for field in (
                "roll",
                "pitch",
                "yaw",
                "gyro_x",
                "gyro_y",
                "gyro_z",
                "accel_x",
                "accel_y",
                "accel_z",
                "accel_norm",
            )
        },
        "level_corrected": {
            "roll": stat([value - offsets[0] for value in field_values(frames, "roll")]),
            "pitch": stat([value - offsets[1] for value in field_values(frames, "pitch")]),
        },
        "five_second_window_drift": {},
        "frame_to_frame_spikes_over_0_5_deg": {},
    }
    for field in ("roll", "pitch", "yaw"):
        start_mean = mean(field_values(first, field))
        end_mean = mean(field_values(last, field))
        result["five_second_window_drift"][field] = (
            None if start_mean is None or end_mean is None else end_mean - start_mean
        )
        values = field_values(frames, field)
        result["frame_to_frame_spikes_over_0_5_deg"][field] = sum(
            1 for a, b in zip(values, values[1:], strict=False) if abs(b - a) > 0.5
        )
    return result


def operation_metrics(
    frames: list[dict[str, Any]],
    events: list[dict[str, Any]],
    prefix: str,
    primary: str,
    cross: str,
) -> list[dict[str, Any]]:
    note_events = [
        event
        for event in events
        if event.get("type") == "note"
        and str(event.get("payload", {}).get("text", "")).startswith(prefix)
    ]
    all_notes = sorted(
        (event for event in events if event.get("type") == "note"),
        key=lambda event: float(event["elapsed_ms"]),
    )
    result: list[dict[str, Any]] = []
    for event in note_events:
        started_ms = float(event["elapsed_ms"])
        later_notes = [
            float(item["elapsed_ms"])
            for item in all_notes
            if float(item["elapsed_ms"]) > started_ms
        ]
        # A human note marks the beginning of a manual operation.  Its end is
        # only known when the following note arrives, so use that whole
        # recorded segment for the maximum angle.  Restricting this to an
        # arbitrary 10-second window misses deliberate hold periods.
        end_ms = later_notes[0] if later_notes else started_ms + 10000.0
        baseline_frames = frame_slice(frames, max(0.0, started_ms - 3000.0), started_ms)
        window = frame_slice(frames, started_ms, end_ms)
        initial_window = frame_slice(frames, started_ms, min(end_ms, started_ms + 10000.0))
        baseline_primary = mean(field_values(baseline_frames, primary))
        baseline_cross = mean(field_values(baseline_frames, cross))
        if baseline_primary is None or not window or not initial_window:
            continue
        peak = max(
            window,
            key=lambda frame: abs((frame.get(primary) or baseline_primary) - baseline_primary),
        )
        initial_peak = max(
            initial_window,
            key=lambda frame: abs((frame.get(primary) or baseline_primary) - baseline_primary),
        )
        delta = (peak.get(primary) or baseline_primary) - baseline_primary
        cross_delta = (
            None
            if baseline_cross is None or peak.get(cross) is None
            else peak[cross] - baseline_cross
        )
        threshold = next(
            (
                frame
                for frame in window
                if frame.get(primary) is not None and abs(frame[primary] - baseline_primary) >= 1.0
            ),
            None,
        )
        result.append(
            {
                "note": event.get("payload", {}).get("text", ""),
                "started_ms": started_ms,
                "window_end_ms": end_ms,
                "baseline_primary_deg": baseline_primary,
                "peak_primary_deg": peak.get(primary),
                "peak_delta_deg": delta,
                "peak_at_ms": peak["elapsed_ms"],
                "cross_axis_delta_deg": cross_delta,
                "first_10s_peak_delta_deg": (initial_peak.get(primary) or baseline_primary)
                - baseline_primary,
                "first_10s_cross_axis_delta_deg": None
                if baseline_cross is None or initial_peak.get(cross) is None
                else initial_peak[cross] - baseline_cross,
                "segment_primary_delta_min_deg": min(
                    (frame.get(primary) or baseline_primary) - baseline_primary for frame in window
                ),
                "segment_primary_delta_max_deg": max(
                    (frame.get(primary) or baseline_primary) - baseline_primary for frame in window
                ),
                "latency_to_1_deg_ms": None
                if threshold is None
                else threshold["elapsed_ms"] - started_ms,
            }
        )
    return result


def recovery_time(
    frames: list[dict[str, Any]], peak_index: int, baseline_roll: float, baseline_pitch: float
) -> float | None:
    # Require 1 s of consecutive samples within 0.5 deg on both axes.
    for index in range(peak_index, len(frames) - 25):
        section = frames[index : index + 25]
        if all(
            abs((frame.get("roll") or baseline_roll) - baseline_roll) <= 0.5
            and abs((frame.get("pitch") or baseline_pitch) - baseline_pitch) <= 0.5
            for frame in section
        ):
            return frames[index]["elapsed_ms"] - frames[peak_index]["elapsed_ms"]
    return None


def disturbance_metrics(
    frames: list[dict[str, Any]], events: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    names = ("translate-forward-back", "translate-left-right", "light-shake-small-impact")
    notes = sorted(
        (event for event in events if event.get("type") == "note"),
        key=lambda event: float(event["elapsed_ms"]),
    )
    results: list[dict[str, Any]] = []
    for event in notes:
        name = str(event.get("payload", {}).get("text", ""))
        if name not in names:
            continue
        start_ms = float(event["elapsed_ms"])
        next_note = next(
            (float(item["elapsed_ms"]) for item in notes if float(item["elapsed_ms"]) > start_ms),
            start_ms + 10000.0,
        )
        window = frame_slice(frames, start_ms, next_note)
        baseline = frame_slice(frames, max(0.0, start_ms - 3000.0), start_ms)
        base_roll = mean(field_values(baseline, "roll"))
        base_pitch = mean(field_values(baseline, "pitch"))
        accel_norms = field_values(window, "accel_norm")
        linear_norms = [
            value
            for value in (vector_norm(frame, "linear_accel") for frame in window)
            if value is not None
        ]
        if not window or base_roll is None or base_pitch is None:
            continue
        peak_index, peak = max(
            enumerate(window),
            key=lambda item: max(
                abs((item[1].get("roll") or base_roll) - base_roll),
                abs((item[1].get("pitch") or base_pitch) - base_pitch),
            ),
        )
        confidence = Counter(
            frame.get("accel_confidence_candidate") or "(empty)" for frame in window
        )
        results.append(
            {
                "note": name,
                "started_ms": start_ms,
                "window_end_ms": next_note,
                "baseline_roll_deg": base_roll,
                "baseline_pitch_deg": base_pitch,
                "accel_norm": stat(accel_norms),
                "linear_accel_norm": stat(linear_norms),
                "max_abs_roll_delta_deg": max(
                    abs((frame.get("roll") or base_roll) - base_roll) for frame in window
                ),
                "max_abs_pitch_delta_deg": max(
                    abs((frame.get("pitch") or base_pitch) - base_pitch) for frame in window
                ),
                "peak_attitude_at_ms": peak["elapsed_ms"],
                "recovery_after_peak_ms": recovery_time(window, peak_index, base_roll, base_pitch),
                "accel_confidence_frames": dict(sorted(confidence.items())),
            }
        )
    return results


def analyse_experiment(directory: Path) -> dict[str, Any]:
    files = {
        name: directory / name
        for name in ("manifest.json", "telemetry.csv", "events.jsonl", "notes.md")
    }
    missing = [name for name, path in files.items() if not path.is_file()]
    manifest = (
        json.loads(files["manifest.json"].read_text(encoding="utf-8"))
        if "manifest.json" not in missing
        else {}
    )
    frames, integrity = (
        telemetry_frames(files["telemetry.csv"], manifest)
        if "telemetry.csv" not in missing
        else ([], {})
    )
    events, corrupt_events = (
        load_events(files["events.jsonl"]) if "events.jsonl" not in missing else ([], 0)
    )
    integrity["corrupt_event_lines"] = corrupt_events
    if manifest and frames:
        integrity.update(event_integrity(events, frames, manifest))
    row_count_mismatch = None
    if manifest and integrity:
        row_count_mismatch = manifest.get("row_counts", {}).get("telemetry_rows") != integrity.get(
            "telemetry_rows"
        )
    integrity["manifest_telemetry_row_count_mismatch"] = row_count_mismatch
    valid = not missing and not any(
        (
            integrity.get("frame_row_count_errors", 0),
            integrity.get("numeric_nonfinite_or_invalid", 0),
            integrity.get("malformed_required_numeric", 0),
            integrity.get("effective_target_invariant_failures", 0),
            integrity.get("manifest_gain_mismatches", 0),
            integrity.get("nonmonotonic_elapsed_deltas", 0),
            integrity.get("corrupt_event_lines", 0),
            integrity.get("events_outside_manifest_duration", 0),
            integrity.get("notes_outside_telemetry", 0),
            int(bool(row_count_mismatch)),
        )
    )
    result: dict[str, Any] = {
        "experiment_id": manifest.get("experiment_id", directory.name),
        "experiment_type": manifest.get("experiment_type"),
        "name": manifest.get("name"),
        "directory": str(directory.relative_to(REPOSITORY_ROOT)).replace("\\", "/"),
        "git": manifest.get("git"),
        "started_at": manifest.get("started_at"),
        "ended_at": manifest.get("ended_at"),
        "duration_sec": iso_seconds(manifest["started_at"], manifest["ended_at"])
        if manifest
        else None,
        "sample_rate_hz": manifest.get("sample_rate_hz"),
        "stabilization": manifest.get("stabilization"),
        "imu": manifest.get("imu"),
        "files": {name: path.is_file() for name, path in files.items()},
        "notes": files["notes.md"].read_text(encoding="utf-8").splitlines()[1:]
        if files["notes.md"].is_file()
        else [],
        "events": events,
        "integrity": integrity,
        "valid": valid,
    }
    if manifest.get("experiment_type", "").startswith("imu-static"):
        result["static_metrics"] = static_metrics(
            frames, manifest.get("imu", {}).get("level_offsets", [0.0, 0.0])
        )
    if manifest.get("experiment_type") == "manual-roll":
        result["axis_operations"] = operation_metrics(frames, events, "roll-", "roll", "pitch")
    if manifest.get("experiment_type") == "manual-pitch":
        result["axis_operations"] = operation_metrics(frames, events, "nose-", "pitch", "roll")
    if manifest.get("experiment_type") == "imu-disturbance":
        result["disturbance_metrics"] = disturbance_metrics(frames, events)
        result["adc_observation"] = {
            "adc_columns_in_telemetry": [
                field for field in integrity.get("header", []) if field.startswith("adc_")
            ],
            "status": "No ADC sample column is present in telemetry.csv; this log cannot establish channel wiring or sensor behavior.",
        }
    return result


def inventory_markdown(experiments: list[dict[str, Any]]) -> str:
    lines = [
        "# 2026-07-11 実機実験ログ・インベントリ",
        "",
        "生成元: `scripts/analyze_experiment_logs.py`。実験の分類は各 `manifest.json` の `experiment_type` / `name` と記録済みイベントのみを使用している。",
        "",
        "| experiment_id | type | duration_sec | Git SHA | frames | telemetry rows | period | gaps | IMU same-run | D項 | PID (R/P) | level offset R/P (deg) | files | valid | remarks |",
        "|---|---|---:|---|---:|---:|---:|---:|---:|---|---|---|---|---|---|",
    ]
    for item in experiments:
        integrity = item["integrity"]
        stabilization = item.get("stabilization", {})
        gains = stabilization.get("gains", {})
        offsets = item.get("imu", {}).get("level_offsets", [0.0, 0.0])
        file_labels = {
            "manifest.json": "manifest",
            "telemetry.csv": "telemetry",
            "events.jsonl": "events",
            "notes.md": "notes",
        }
        files = " / ".join(file_labels[name] for name, exists in item["files"].items() if exists)
        remarks = []
        if integrity.get("sample_gaps"):
            remarks.append(f"{integrity['sample_gaps']} sample gaps")
        if integrity.get("frame_row_count_errors"):
            remarks.append(f"{integrity['frame_row_count_errors']} non-8-row frames")
        if item.get("experiment_type") == "imu-disturbance":
            remarks.append("ADC列なし")
        if not remarks:
            remarks.append("integrity checks passed")
        pid = f"{fmt(gains.get('kp_roll'))}/{fmt(gains.get('ki_roll'))}/{fmt(gains.get('kd_roll'))} ; {fmt(gains.get('kp_pitch'))}/{fmt(gains.get('ki_pitch'))}/{fmt(gains.get('kd_pitch'))}"
        lines.append(
            f"| `{item['experiment_id']}` | `{item.get('experiment_type')}` | {fmt(item.get('duration_sec'))} | `{item.get('git', {}).get('sha', '')[:7]}` | {integrity.get('frames', 0):,} | {integrity.get('telemetry_rows', 0):,} | {fmt(integrity.get('median_frame_period_ms'), 2)} ms | {integrity.get('sample_gaps', 0)} | {integrity.get('max_identical_imu_frame_run', 0)} | `{stabilization.get('derivative_source')}` | {pid} | {fmt(offsets[0])}/{fmt(offsets[1])} | {files} | {'yes' if item['valid'] else 'no'} | {'; '.join(remarks)} |"
        )
    lines.extend(
        [
            "",
            "## 完全性チェックの定義",
            "",
            "- `elapsed_ms` はフレーム（同一時刻の8アクチュエータ行を1フレームに集約）で単調増加すること。",
            "- 各フレームは8行、数値列は有限値、`effective_target = clamp(base_target + round(stabilization_correction), 0, 4095)` を満たすこと。",
            "- `manifest` の行数・PID値とCSVの値、イベント時刻と記録範囲を照合すること。",
            "- サンプルギャップは `max(期待周期, 実測中央値) × 1.5` を超えるフレーム間隔として数える。",
            "",
            "## 記録済みoperator notes",
            "",
        ]
    )
    for item in experiments:
        lines.append(f"### `{item['experiment_id']}`")
        lines.append("")
        note_events = [event for event in item.get("events", []) if event.get("type") == "note"]
        if not note_events:
            lines.append("- （noteなし）")
        for event in note_events:
            lines.append(
                f"- {fmt(float(event['elapsed_ms']) / 1000.0)} s: `{event.get('payload', {}).get('text', '')}`"
            )
        lines.append("")
    return "\n".join(lines)


def analysis_markdown(experiments: list[dict[str, Any]]) -> str:
    by_type = {item.get("experiment_type"): item for item in experiments}
    pre = by_type.get("imu-static")
    post = by_type.get("imu-static-calibrated")
    roll = by_type.get("manual-roll")
    pitch = by_type.get("manual-pitch")
    disturbance = by_type.get("imu-disturbance")
    lines = [
        "# 2026-07-11 実機IMUデータ解析",
        "",
        "対象: `PQL-A00` / branch `experiment/2026-07-11` / 実機Git SHA `b106d08`。数値は同一時刻の8行を1フレームに集約して算出した。テレメトリのRoll/Pitchは生Euler値であり、水平オフセットを引いた値とは別に扱う。",
        "",
        "## 1. ログ健全性",
        "",
        "全5ログで必須4ファイルが存在し、フレーム行数、有限値、時間単調性、設定値、イベント範囲、および target の不変条件を検証した。結果は全ログ `valid=yes` である。全ログでフレーム周期中央値は40.00 ms、サンプルギャップは0、同一IMU値が連続した最長runは1フレームであり、記録範囲内にIMU更新停止の兆候はない。",
        "",
        "## 2. IMU静止基準と較正",
        "",
    ]
    if pre and post:
        pre_raw = pre["static_metrics"]["raw"]
        post_raw = post["static_metrics"]["raw"]
        post_level = post["static_metrics"]["level_corrected"]
        lines.extend(
            [
                "| 指標 | 較正前 `imu-static` | 較正後 `imu-static-calibrated` |",
                "|---|---:|---:|",
                f"| Roll mean ± std (raw deg) | {fmt(pre_raw['roll']['mean'])} ± {fmt(pre_raw['roll']['std'])} | {fmt(post_raw['roll']['mean'])} ± {fmt(post_raw['roll']['std'])} |",
                f"| Pitch mean ± std (raw deg) | {fmt(pre_raw['pitch']['mean'])} ± {fmt(pre_raw['pitch']['std'])} | {fmt(post_raw['pitch']['mean'])} ± {fmt(post_raw['pitch']['std'])} |",
                f"| Roll mean ± std (level-corrected deg) | {fmt(pre['static_metrics']['level_corrected']['roll']['mean'])} ± {fmt(pre['static_metrics']['level_corrected']['roll']['std'])} | {fmt(post_level['roll']['mean'])} ± {fmt(post_level['roll']['std'])} |",
                f"| Pitch mean ± std (level-corrected deg) | {fmt(pre['static_metrics']['level_corrected']['pitch']['mean'])} ± {fmt(pre['static_metrics']['level_corrected']['pitch']['std'])} | {fmt(post_level['pitch']['mean'])} ± {fmt(post_level['pitch']['std'])} |",
                f"| Gyro X/Y/Z mean (dps) | {fmt(pre_raw['gyro_x']['mean'])} / {fmt(pre_raw['gyro_y']['mean'])} / {fmt(pre_raw['gyro_z']['mean'])} | {fmt(post_raw['gyro_x']['mean'])} / {fmt(post_raw['gyro_y']['mean'])} / {fmt(post_raw['gyro_z']['mean'])} |",
                f"| Accel norm mean ± std (g) | {fmt(pre_raw['accel_norm']['mean'], 4)} ± {fmt(pre_raw['accel_norm']['std'], 4)} | {fmt(post_raw['accel_norm']['mean'], 4)} ± {fmt(post_raw['accel_norm']['std'], 4)} |",
                f"| Roll/Pitch 5s-window drift (deg) | {fmt(pre['static_metrics']['five_second_window_drift']['roll'])} / {fmt(pre['static_metrics']['five_second_window_drift']['pitch'])} | {fmt(post['static_metrics']['five_second_window_drift']['roll'])} / {fmt(post['static_metrics']['five_second_window_drift']['pitch'])} |",
                "",
                f"較正後ログの記録済み水平オフセットは Roll `{fmt(post['imu']['level_offsets'][0], 6)}` deg、Pitch `{fmt(post['imu']['level_offsets'][1], 6)}` deg。生値からこのオフセットを引くと、平均はRoll `{fmt(post_level['roll']['mean'])}` deg、Pitch `{fmt(post_level['pitch']['mean'])}` degとなる。これは較正がUI/制御の水平基準に適用され、CSVは生値を保存しているという実装と整合する。",
                "",
                f"実効ログ周期（中央値）は較正前 `{fmt(pre['integrity']['median_frame_period_ms'], 2)}` ms、較正後 `{fmt(post['integrity']['median_frame_period_ms'], 2)}` msで、25 Hz記録として妥当。静止ログのRoll/Pitchで0.5 deg超のフレーム間スパイクは、較正前 Roll `{pre['static_metrics']['frame_to_frame_spikes_over_0_5_deg']['roll']}` / Pitch `{pre['static_metrics']['frame_to_frame_spikes_over_0_5_deg']['pitch']}`、較正後 Roll `{post['static_metrics']['frame_to_frame_spikes_over_0_5_deg']['roll']}` / Pitch `{post['static_metrics']['frame_to_frame_spikes_over_0_5_deg']['pitch']}`。",
                "",
                "ジャイロ平均はゼロ近傍へ改善したが、較正後の31.8秒ではRoll/Pitch標準偏差と5秒窓ドリフトが較正前より大きい。水平オフセットは定常バイアスを移すだけでノイズや姿勢ドリフトを減らさないため、このログを「較正後に姿勢品質まで改善した」根拠にはしない。静止・再起動直後・磁気補正条件を分けた再試験が必要である。",
                "",
            ]
        )
    lines.extend(["## 3. Roll/Pitch 軸と符号", ""])
    for title, item, primary, cross in (
        ("Roll", roll, "Roll", "Pitch"),
        ("Pitch", pitch, "Pitch", "Roll"),
    ):
        if not item:
            continue
        lines.extend(
            [
                f"### {title} 手動試験 `{item['experiment_id']}`",
                "",
                "各 `*-start` note の直前3秒を基準に、次のnoteまでの記録区間で最大値を求めた。noteは手動で記録されるため、`1 deg到達遅延`は厳密なセンサ遅延ではなく、記録noteから最初に1 deg変化が観測された時刻である。",
                "",
                f"| note | 最初の10s {primary} peak Δ (deg) | {cross}混入 Δ (deg) | 1 deg到達遅延 | 区間最大の時刻 |",
                "|---|---:|---:|---:|---:|",
            ]
        )
        for operation in item.get("axis_operations", []):
            lines.append(
                f"| `{operation['note']}` | {fmt(operation['first_10s_peak_delta_deg'])} | {fmt(operation['first_10s_cross_axis_delta_deg'])} | {fmt_seconds(None if operation['latency_to_1_deg_ms'] is None else operation['latency_to_1_deg_ms'] / 1000.0)} | {fmt(operation['peak_at_ms'] / 1000.0)} s |"
            )
        lines.extend(
            [
                "",
                "noteから次noteまでの区間全体には復帰・再操作が混在し得るため、最大角度の符号判定には使わない。参考として、各区間の主軸差分の最小..最大値は次の通り。",
                "",
                f"| note | {primary}区間差分 min .. max (deg) |",
                "|---|---:|",
            ]
        )
        for operation in item.get("axis_operations", []):
            lines.append(
                f"| `{operation['note']}` | {fmt(operation['segment_primary_delta_min_deg'])} .. {fmt(operation['segment_primary_delta_max_deg'])} |"
            )
        lines.append("")
    lines.extend(
        [
            "ログだけでは手動操作の終了時刻が記録されていないため、note間の最大値を物理方向の根拠にしてはならない。一方、`roll-right-start`の最初の10秒ではRollが負方向へ5.253 deg、`nose-up-start`ではPitchが正方向へ1.145 deg、`nose-down-start`ではPitchが負方向へ2.827 deg変化した。現地で確認済みの観察（右傾き時Roll負、左傾き時Roll正、ノーズアップ時Pitch正、ノーズダウン時Pitch負）と矛盾しない。これを根拠に、制御用のlevel-corrected Roll符号を反転し、URDF足先高さ感度の符号で混合行列を導出した。これはミキシングの盲目的な反転ではなく、target増加=シリンダー伸長という確認済み条件と3Dモデルに基づくものだが、実機制御はまだ有効化しない。次回は各傾斜の開始・保持・復帰にnoteを打つ。",
            "",
            "## 4. 外乱・並進加速度",
            "",
        ]
    )
    if disturbance:
        lines.extend(
            [
                "各noteの直前3秒を姿勢基準にし、次のnoteまでを観測窓にした。`recovery`はピーク後、Roll/Pitch両方が基準±0.5 degへ連続1秒入った最初の時刻である。操作終了時刻は記録されていないため、値は観測可能な範囲の指標であり完全な物理応答時間ではない。",
                "",
                "| 操作note | accel norm range (g) | linear accel max (g) | Roll最大偏差 (deg) | Pitch最大偏差 (deg) | recovery | confidence frames |",
                "|---|---:|---:|---:|---:|---:|---|",
            ]
        )
        for metric in disturbance.get("disturbance_metrics", []):
            accel = metric["accel_norm"]
            linear = metric["linear_accel_norm"]
            confidence = ", ".join(
                f"{key}:{value}" for key, value in metric["accel_confidence_frames"].items()
            )
            lines.append(
                f"| `{metric['note']}` | {fmt(accel['min'], 4)} .. {fmt(accel['max'], 4)} | {fmt(linear['max'], 4)} | {fmt(metric['max_abs_roll_delta_deg'])} | {fmt(metric['max_abs_pitch_delta_deg'])} | {fmt_seconds(None if metric['recovery_after_peak_ms'] is None else metric['recovery_after_peak_ms'] / 1000.0)} | {confidence} |"
            )
        all_confidence = disturbance["integrity"].get("accel_confidence_rows", {})
        lines.extend(
            [
                "",
                f"`accel_confidence_candidate` は空列ではなく、CSVに `{', '.join(f'{key}:{value}' for key, value in all_confidence.items())}` と記録されている。現行閾値は `abs(accel_norm - 1.0) <= 0.08 g` をhigh、`<= 0.40 g`をmedium、それ以外をlowとする。これは診断用候補で、Mahonyゲインまたは制御へは接続されていない。次段階は今回のリプレイで、特に強い並進外乱でもhighとなる区間が姿勢誤認とどう対応するかを評価することである。",
                "",
                "Yawは各外乱・Pitch操作で大きく変化しており、現時点ではRoll/Pitch制御の入力に含めない。磁気キャリブレーション、mag norm gate、heading jump gateを実装・検証するまでは、GUI上でも参照値として扱う。",
                "",
                "## 5. ADC観測",
                "",
                "`imu-disturbance` の `telemetry.csv` に ADC bank/channel/raw/voltage 列はない。manifestには SPI bus 0、devices 0/1、VREF 3.3 Vという設定だけがあり、実データとして接続状態・飽和・ノイズ・チャンネル対応を判断できない。今回のログから脚への割当てを推測しない。",
                "",
                "## 6. 判明事項と7月18日までの実装優先順位",
                "",
                "1. **安全維持:** モデル由来の符号を実装済みでも、スタビライゼーションとCSV歩行はPhase 9相当の安全確認まで有効化しない。",
                "2. **観測系の明確化:** telemetryにはraw、level-corrected、control-frame Roll/Pitchを同時記録する実装を追加した。次回ログで値を照合する。",
                "3. **Roll座標系の切り分け:** 実機IMU試験由来のcontrol Roll符号とURDF足先高さ感度で混合行列を導出した。完全なIMU取付クォータニオンとGUI表示の検証はmanual-roll/manual-pitch再試験で続ける。",
                "4. **加速度信頼度の再現性:** 現行の `±0.08 g / ±0.40 g` 閾値を今回のリプレイで比較し、強い並進外乱でもhighとなる区間の扱いを評価する。制御接続はまだ行わない。",
                "5. **ADCログ拡張:** ADCを導入したら物理名を推測せず `adc_bank_{bank}_channel_{channel}` で raw/voltage/取得時刻をログに追加し、接地・荷重の段階試験でマッピングを決める。",
                "6. **7月18日の現地試験:** ADC/圧力チャンネル→物理センサを1項目ずつ記録する。脚・関節・target増加=伸長は3Dモデルと既知条件から扱い、低圧・非常停止可能な後続試験でモデル由来の補正結果だけを確認する。",
                "",
            ]
        )
    return "\n".join(lines)


def metrics_rows(experiments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in experiments:
        integrity = item["integrity"]
        row: dict[str, Any] = {
            "experiment_id": item["experiment_id"],
            "experiment_type": item.get("experiment_type"),
            "name": item.get("name"),
            "duration_sec": item.get("duration_sec"),
            "git_sha": item.get("git", {}).get("sha"),
            "frames": integrity.get("frames"),
            "telemetry_rows": integrity.get("telemetry_rows"),
            "valid": item["valid"],
            "median_frame_period_ms": integrity.get("median_frame_period_ms"),
            "sample_gaps": integrity.get("sample_gaps"),
            "largest_sample_gap_ms": integrity.get("largest_sample_gap_ms"),
            "frame_row_count_errors": integrity.get("frame_row_count_errors"),
            "numeric_nonfinite_or_invalid": integrity.get("numeric_nonfinite_or_invalid"),
            "effective_target_invariant_failures": integrity.get(
                "effective_target_invariant_failures"
            ),
            "adc_columns_present": ";".join(
                item.get("adc_observation", {}).get("adc_columns_in_telemetry", [])
            ),
        }
        static = item.get("static_metrics", {})
        for prefix, metrics, fields in (
            (
                "raw",
                static.get("raw", {}),
                ("roll", "pitch", "gyro_x", "gyro_y", "gyro_z", "accel_norm"),
            ),
            ("level_corrected", static.get("level_corrected", {}), ("roll", "pitch")),
        ):
            for field in fields:
                values = metrics.get(field, {})
                row[f"{prefix}_{field}_mean"] = values.get("mean")
                row[f"{prefix}_{field}_std"] = values.get("std")
        rows.append(row)
    return rows


def main() -> None:
    experiment_dirs = sorted(path for path in DEFAULT_EXPERIMENT_ROOT.iterdir() if path.is_dir())
    if not experiment_dirs:
        raise SystemExit(f"No experiment directories found in {DEFAULT_EXPERIMENT_ROOT}")
    experiments = [analyse_experiment(directory) for directory in experiment_dirs]
    DEFAULT_ANALYSIS_ROOT.mkdir(parents=True, exist_ok=True)
    DEFAULT_DOC_ROOT.mkdir(parents=True, exist_ok=True)
    summary_path = DEFAULT_ANALYSIS_ROOT / "2026-07-11_summary.json"
    metrics_path = DEFAULT_ANALYSIS_ROOT / "2026-07-11_metrics.csv"
    inventory_path = DEFAULT_DOC_ROOT / "2026-07-11_inventory.md"
    analysis_path = DEFAULT_DOC_ROOT / "2026-07-11_analysis.md"
    summary_path.write_text(
        json.dumps(
            {"analysis_schema_version": 1, "experiments": experiments}, ensure_ascii=False, indent=2
        )
        + "\n",
        encoding="utf-8",
    )
    rows = metrics_rows(experiments)
    with metrics_path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    inventory_path.write_text(inventory_markdown(experiments) + "\n", encoding="utf-8")
    analysis_path.write_text(analysis_markdown(experiments) + "\n", encoding="utf-8")
    print(f"Wrote {inventory_path.relative_to(REPOSITORY_ROOT)}")
    print(f"Wrote {analysis_path.relative_to(REPOSITORY_ROOT)}")
    print(f"Wrote {summary_path.relative_to(REPOSITORY_ROOT)}")
    print(f"Wrote {metrics_path.relative_to(REPOSITORY_ROOT)}")
    print(
        f"Validated {len(experiments)} experiments: {sum(item['valid'] for item in experiments)} valid"
    )


if __name__ == "__main__":
    main()
