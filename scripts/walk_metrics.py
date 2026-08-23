"""Walking metrics from experiment telemetry.csv (stdlib only).

Usage:
    python scripts/walk_metrics.py metrics <run_dir> [--json]
    python scripts/walk_metrics.py compare <run_dir_a> <run_dir_b>

`metrics` computes, per run:
  * per-axis pneumatic lag (cross-correlation of effective_target vs
    actual_position), saturation %, rate-limit duty
  * rear-leg kick sync delta (RR vs RL hip and knee lag difference)
  * control-frame roll/pitch RMS and max
  * walk cycle count / duration stats and per-leg contact duty (when the
    walk/contact columns exist; older logs degrade gracefully)
and writes them to <run_dir>/metrics.json.

`compare` prints A/B deltas of two runs' metrics (computing them if needed).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from pathlib import Path

EXPECTED_ACTUATORS = 8
POSITION_MIN = 0
POSITION_MAX = 4095
MAX_LAG_S = 1.5
# Serial/control ordering (see pql_a00_kinematics.ACTUATOR_HEIGHT_EFFECTS).
AXIS_NAMES = (
    "FR hip",
    "FR knee",
    "FL hip",
    "FL knee",
    "RR hip",
    "RR knee",
    "RL hip",
    "RL knee",
)
CONTACT_LEGS = ("fr", "fl", "rr", "rl")


def _num(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def _num_or(value: str | None, default: float) -> float:
    parsed = _num(value)
    return default if parsed is None else parsed


def load_run(run_dir: Path) -> dict:
    """Parse telemetry.csv into per-axis and per-tick series."""
    path = run_dir / "telemetry.csv"
    if not path.exists():
        raise SystemExit(f"telemetry.csv not found in {run_dir}")

    axes: dict[int, dict[str, list[float]]] = {
        axis: {"t": [], "actual": [], "effective": [], "rate_limited": [], "saturated": []}
        for axis in range(EXPECTED_ACTUATORS)
    }
    ticks: dict[str, list[float]] = {
        "t": [],
        "roll": [],
        "pitch": [],
        "walk_active": [],
        "walk_phase": [],
        "walk_cycle": [],
        "walk_scale": [],
    }
    contact: dict[str, list[float]] = {leg: [] for leg in CONTACT_LEGS}
    seen_ticks: set[str] = set()
    has_walk_cols = False
    has_contact_cols = False

    with path.open("r", encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        header = reader.fieldnames or []
        has_walk_cols = "walk_phase" in header
        has_contact_cols = "contact_fr" in header
        for row in reader:
            elapsed = _num(row.get("elapsed_ms"))
            actuator = _num(row.get("actuator_id"))
            if elapsed is None or actuator is None:
                continue
            axis = int(actuator)
            if axis not in axes:
                continue
            actual = _num(row.get("actual_position"))
            effective = _num(row.get("effective_target"))
            if actual is None or effective is None:
                continue
            series = axes[axis]
            series["t"].append(elapsed / 1000.0)
            series["actual"].append(actual)
            series["effective"].append(effective)
            if has_walk_cols:
                series["rate_limited"].append(_num_or(row.get("walk_rate_limited"), 0.0))
                series["saturated"].append(_num_or(row.get("walk_saturated"), 0.0))

            key = row["elapsed_ms"]
            if key in seen_ticks:
                continue
            seen_ticks.add(key)
            ticks["t"].append(elapsed / 1000.0)
            ticks["roll"].append(_num_or(row.get("control_roll"), 0.0))
            ticks["pitch"].append(_num_or(row.get("control_pitch"), 0.0))
            if has_walk_cols:
                ticks["walk_active"].append(_num_or(row.get("walk_active"), 0.0))
                # -1 marks "not walking" ticks; 0.0 is a legitimate phase/cycle.
                ticks["walk_phase"].append(_num_or(row.get("walk_phase"), -1.0))
                ticks["walk_cycle"].append(_num_or(row.get("walk_cycle"), -1.0))
                ticks["walk_scale"].append(_num_or(row.get("walk_motion_scale"), 0.0))
            if has_contact_cols:
                for leg in CONTACT_LEGS:
                    contact[leg].append(_num_or(row.get(f"contact_{leg}"), 0.0))

    return {
        "axes": axes,
        "ticks": ticks,
        "contact": contact,
        "has_walk_cols": has_walk_cols,
        "has_contact_cols": has_contact_cols,
    }


def _sample_period(times: list[float]) -> float | None:
    deltas = [b - a for a, b in zip(times, times[1:], strict=False) if b > a]
    return statistics.median(deltas) if deltas else None


def _correlation(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    if n < 8:
        return 0.0
    a, b = a[:n], b[:n]
    mean_a, mean_b = statistics.fmean(a), statistics.fmean(b)
    num = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b, strict=False))
    den_a = math.sqrt(sum((x - mean_a) ** 2 for x in a))
    den_b = math.sqrt(sum((y - mean_b) ** 2 for y in b))
    if den_a == 0.0 or den_b == 0.0:
        return 0.0
    return num / (den_a * den_b)


def estimate_lag_s(command: list[float], actual: list[float], period: float) -> float | None:
    """Lag (s) that maximizes correlation of actual against a shifted command."""
    if period is None or period <= 0.0 or len(command) < 16:
        return None
    max_shift = min(int(MAX_LAG_S / period), len(command) - 8)
    best_shift, best_corr = 0, -2.0
    for shift in range(max_shift + 1):
        corr = _correlation(command[: len(command) - shift], actual[shift:])
        if corr > best_corr:
            best_corr, best_shift = corr, shift
    if best_corr < 0.3:  # ponytail: flat/noise series -> no meaningful lag
        return None
    return best_shift * period


def compute_metrics(run_dir: Path) -> dict:
    data = load_run(run_dir)
    axes = data["axes"]
    ticks = data["ticks"]

    axis_metrics = []
    lags: dict[int, float | None] = {}
    for axis in range(EXPECTED_ACTUATORS):
        series = axes[axis]
        period = _sample_period(series["t"])
        lag = estimate_lag_s(series["effective"], series["actual"], period)
        lags[axis] = lag
        total = len(series["actual"])
        saturation = (
            sum(
                1
                for value in series["actual"]
                if value <= POSITION_MIN or value >= POSITION_MAX
            )
            / total
            if total
            else None
        )
        rate_duty = (
            statistics.fmean(series["rate_limited"]) if series["rate_limited"] else None
        )
        cmd_saturation = (
            statistics.fmean(series["saturated"]) if series["saturated"] else None
        )
        axis_metrics.append(
            {
                "axis": axis,
                "name": AXIS_NAMES[axis],
                "samples": total,
                "lag_s": lag,
                "actual_saturation": saturation,
                "command_saturation": cmd_saturation,
                "rate_limit_duty": rate_duty,
            }
        )

    def _sync(axis_a: int, axis_b: int) -> float | None:
        if lags[axis_a] is None or lags[axis_b] is None:
            return None
        return abs(lags[axis_a] - lags[axis_b])

    roll = ticks["roll"]
    pitch = ticks["pitch"]
    attitude = {
        "roll_rms_deg": math.sqrt(statistics.fmean(v * v for v in roll)) if roll else None,
        "pitch_rms_deg": math.sqrt(statistics.fmean(v * v for v in pitch)) if pitch else None,
        "roll_max_abs_deg": max((abs(v) for v in roll), default=None),
        "pitch_max_abs_deg": max((abs(v) for v in pitch), default=None),
    }

    cycles = None
    if data["has_walk_cols"] and ticks["walk_phase"]:
        # Count phase wraps directly: the controller's final cycle increment
        # lands after the last recorded sample, so the walk_cycle column
        # under-reports by one on cycle-bounded runs.
        # Only full-amplitude cycles count (matches the controller's rule of
        # starting the cycle counter after the ramp completes).
        full_scale = max(ticks["walk_scale"], default=0.0)
        wrap_times: list[float] = []
        previous_phase: float | None = None
        for t, phase, scale in zip(
            ticks["t"], ticks["walk_phase"], ticks["walk_scale"], strict=False
        ):
            if phase < 0 or scale < full_scale - 1e-6:
                previous_phase = None
                continue
            if previous_phase is not None and phase < previous_phase:
                wrap_times.append(t)
            previous_phase = phase
        if any(phase >= 0 for phase in ticks["walk_phase"]):
            durations = [
                b - a for a, b in zip(wrap_times, wrap_times[1:], strict=False)
            ]
            cycles = {
                "completed": len(wrap_times),
                "duration_mean_s": statistics.fmean(durations) if durations else None,
                "duration_stdev_s": (
                    statistics.stdev(durations) if len(durations) > 1 else None
                ),
            }

    contact_duty = None
    if data["has_contact_cols"]:
        contact_duty = {
            leg: (statistics.fmean(values) if values else None)
            for leg, values in data["contact"].items()
        }

    metrics = {
        "run": run_dir.name,
        "axes": axis_metrics,
        "rear_kick_sync": {
            "hip_lag_delta_s": _sync(4, 6),
            "knee_lag_delta_s": _sync(5, 7),
        },
        "attitude": attitude,
        "cycles": cycles,
        "contact_duty": contact_duty,
        "has_walk_cols": data["has_walk_cols"],
        "has_contact_cols": data["has_contact_cols"],
    }
    (run_dir / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return metrics


def _fmt(value: float | None, digits: int = 3, suffix: str = "") -> str:
    if value is None:
        return "-"
    return f"{value:.{digits}f}{suffix}"


def print_metrics(metrics: dict) -> None:
    print(f"== {metrics['run']} ==")
    print(f"{'axis':<9}{'lag':>8}{'sat(act)':>10}{'sat(cmd)':>10}{'rate-lim':>10}")
    for axis in metrics["axes"]:
        print(
            f"{axis['name']:<9}"
            f"{_fmt(axis['lag_s'], 2, 's'):>8}"
            f"{_fmt(axis['actual_saturation'] and axis['actual_saturation'] * 100, 1, '%'):>10}"
            f"{_fmt(axis['command_saturation'] and axis['command_saturation'] * 100, 1, '%'):>10}"
            f"{_fmt(axis['rate_limit_duty'] and axis['rate_limit_duty'] * 100, 1, '%'):>10}"
        )
    sync = metrics["rear_kick_sync"]
    print(
        f"rear kick sync delta: hip={_fmt(sync['hip_lag_delta_s'], 2, 's')} "
        f"knee={_fmt(sync['knee_lag_delta_s'], 2, 's')}"
    )
    att = metrics["attitude"]
    print(
        f"attitude: roll RMS={_fmt(att['roll_rms_deg'], 2)}deg "
        f"max={_fmt(att['roll_max_abs_deg'], 2)}deg / "
        f"pitch RMS={_fmt(att['pitch_rms_deg'], 2)}deg "
        f"max={_fmt(att['pitch_max_abs_deg'], 2)}deg"
    )
    if metrics["cycles"]:
        cyc = metrics["cycles"]
        print(
            f"cycles: {cyc['completed']} completed, "
            f"duration {_fmt(cyc['duration_mean_s'], 2, 's')} "
            f"(stdev {_fmt(cyc['duration_stdev_s'], 3, 's')})"
        )
    if metrics["contact_duty"]:
        duty = ", ".join(
            f"{leg.upper()}={_fmt(value and value * 100, 0, '%')}"
            for leg, value in metrics["contact_duty"].items()
        )
        print(f"contact duty: {duty}")
    if not metrics["has_walk_cols"]:
        print("(legacy log: no walk/contact columns -> walk metrics skipped)")


def _load_or_compute(run_dir: Path) -> dict:
    cached = run_dir / "metrics.json"
    if cached.exists():
        return json.loads(cached.read_text(encoding="utf-8"))
    return compute_metrics(run_dir)


def cmd_compare(dir_a: Path, dir_b: Path) -> None:
    a, b = _load_or_compute(dir_a), _load_or_compute(dir_b)
    print(f"== {a['run']}  vs  {b['run']} ==")
    print(f"{'axis':<9}{'lag A':>8}{'lag B':>8}{'delta':>8}")
    for row_a, row_b in zip(a["axes"], b["axes"], strict=False):
        delta = (
            row_b["lag_s"] - row_a["lag_s"]
            if row_a["lag_s"] is not None and row_b["lag_s"] is not None
            else None
        )
        print(
            f"{row_a['name']:<9}"
            f"{_fmt(row_a['lag_s'], 2, 's'):>8}"
            f"{_fmt(row_b['lag_s'], 2, 's'):>8}"
            f"{_fmt(delta, 2, 's'):>8}"
        )
    for key in ("hip_lag_delta_s", "knee_lag_delta_s"):
        va, vb = a["rear_kick_sync"][key], b["rear_kick_sync"][key]
        print(f"rear {key}: A={_fmt(va, 2, 's')} B={_fmt(vb, 2, 's')}")
    for key in ("roll_rms_deg", "pitch_rms_deg"):
        va, vb = a["attitude"][key], b["attitude"][key]
        delta = vb - va if va is not None and vb is not None else None
        print(f"{key}: A={_fmt(va, 2)} B={_fmt(vb, 2)} delta={_fmt(delta, 2)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    metrics_parser = sub.add_parser("metrics", help="Compute metrics for one run")
    metrics_parser.add_argument("run_dir", type=Path)
    metrics_parser.add_argument("--json", action="store_true", help="Print raw JSON")
    compare_parser = sub.add_parser("compare", help="Compare two runs")
    compare_parser.add_argument("run_dir_a", type=Path)
    compare_parser.add_argument("run_dir_b", type=Path)
    args = parser.parse_args(argv)

    if args.command == "metrics":
        metrics = compute_metrics(args.run_dir)
        if args.json:
            print(json.dumps(metrics, ensure_ascii=False, indent=2))
        else:
            print_metrics(metrics)
            print(f"wrote {args.run_dir / 'metrics.json'}")
    else:
        cmd_compare(args.run_dir_a, args.run_dir_b)
    return 0


if __name__ == "__main__":
    sys.exit(main())
