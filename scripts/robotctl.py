"""robotctl — stdlib-only CLI for the PQL-A00 control server's HTTP API.

Talks to a running ``highend_server`` instance over plain HTTP (no
third-party dependencies, so it works from a bare Python interpreter on the
Pi or a dev machine). See ``python scripts/robotctl.py --help``.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.error
import urllib.request
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from preflight import DEFAULT_HOST, format_results, normalize_base_url, run_preflight  # noqa: E402

# --------------------------------------------------------------------------
# HTTP helpers
# --------------------------------------------------------------------------


def _extract_error_detail(raw: bytes, code: int) -> str:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return raw.decode("utf-8", "replace") or f"HTTP {code}"
    detail = data.get("detail") if isinstance(data, dict) else None
    return str(detail) if detail is not None else f"HTTP {code}"


def _perform(request: urllib.request.Request, timeout: float) -> dict:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            raw = response.read()
    except urllib.error.HTTPError as error:
        detail = _extract_error_detail(error.read(), error.code)
        print(f"[NG] HTTP {error.code}: {detail}")
        sys.exit(1)
    except urllib.error.URLError as error:
        print(f"[NG] cannot reach server: {error.reason}")
        print("Is the server running? Check --host (default 127.0.0.1:8000).")
        sys.exit(1)
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def api_get(base_url: str, path: str, timeout: float = 5.0) -> dict:
    url = normalize_base_url(base_url) + path
    request = urllib.request.Request(url, method="GET")  # noqa: S310
    return _perform(request, timeout)


def api_post(base_url: str, path: str, body: dict | None = None, timeout: float = 10.0) -> dict:
    url = normalize_base_url(base_url) + path
    data = json.dumps(body or {}).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310
        url, data=data, method="POST", headers={"Content-Type": "application/json"}
    )
    return _perform(request, timeout)


# --------------------------------------------------------------------------
# Formatting helpers
# --------------------------------------------------------------------------


def _vector_norm(vector: dict) -> float:
    return math.sqrt(sum(float(vector.get(axis, 0.0)) ** 2 for axis in ("x", "y", "z")))


def _fmt_optional(value: float | None, precision: int = 2) -> str:
    return "-" if value is None else f"{value:.{precision}f}"


def human_bytes(count: int) -> str:
    size = float(count)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0 or unit == "TB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"


def human_duration(seconds: float) -> str:
    seconds = max(0.0, seconds)
    total_seconds = int(seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes}m{secs}s"
    if minutes:
        return f"{minutes}m{secs}s"
    return f"{seconds:.1f}s"


# --------------------------------------------------------------------------
# Subcommands
# --------------------------------------------------------------------------


def cmd_status(base_url: str) -> int:
    health = api_get(base_url, "/api/health")
    system = health.get("system") or {}
    stab = api_get(base_url, "/api/control/stabilization")
    sensors = api_get(base_url, "/api/sensors").get("item") or {}
    imu = sensors.get("imu") or {}

    mode = "emulated" if system.get("emulate_devices") else "hardware"
    print(f"[{'OK' if health.get('ok') else 'NG'}] server: connected ({mode})")
    print(f"playback: {system.get('playback_status')}")
    recording = "on" if system.get("telemetry_recording") else "off"
    print(f"recording: {recording}")
    print(
        f"stabilization: enabled={stab.get('enabled')} active={stab.get('active')} "
        f"reason={stab.get('disabled_reason') or '-'}"
    )
    print(
        f"attitude: roll={_fmt_optional(stab.get('roll_deg'))} "
        f"pitch={_fmt_optional(stab.get('pitch_deg'))}"
    )
    print(f"IMU: {imu.get('connection_state', 'unknown')}")
    return 0


def cmd_sensors(base_url: str) -> int:
    item = api_get(base_url, "/api/sensors").get("item") or {}
    imu = item.get("imu") or {}
    orientation = imu.get("orientation") or {}
    gyro = imu.get("gyro_dps") or {}
    accel = imu.get("accel_g") or {}
    mag = imu.get("mag_raw")

    print(
        f"orientation (deg): roll={_fmt_optional(orientation.get('roll_deg'))} "
        f"pitch={_fmt_optional(orientation.get('pitch_deg'))} "
        f"yaw={_fmt_optional(orientation.get('yaw_deg'))}"
    )
    print(
        f"gyro (dps): x={_fmt_optional(gyro.get('x'))} y={_fmt_optional(gyro.get('y'))} "
        f"z={_fmt_optional(gyro.get('z'))}"
    )
    print(
        f"accel (g): x={_fmt_optional(accel.get('x'), 3)} y={_fmt_optional(accel.get('y'), 3)} "
        f"z={_fmt_optional(accel.get('z'), 3)} norm={_vector_norm(accel):.3f}"
    )
    print(f"mag_valid: {mag is not None}")
    print(f"temperature_c: {_fmt_optional(imu.get('temperature_c'))}")
    print(f"sample_count: {imu.get('sample_count', 0)}")
    return 0


def cmd_stabilization_status(base_url: str) -> int:
    state = api_get(base_url, "/api/control/stabilization")
    print(
        f"enabled={state.get('enabled')} active={state.get('active')} "
        f"auto_disabled={state.get('auto_disabled')} reason={state.get('disabled_reason') or '-'}"
    )
    gains = state.get("gains") or {}
    print("gains:")
    print(f"  roll : kp={gains.get('kp_roll')} ki={gains.get('ki_roll')} kd={gains.get('kd_roll')}")
    print(
        f"  pitch: kp={gains.get('kp_pitch')} ki={gains.get('ki_pitch')} kd={gains.get('kd_pitch')}"
    )
    print("corrections:")
    corrections = state.get("corrections") or []
    if not corrections:
        print("  (none)")
    for correction in corrections:
        print(
            f"  [{correction.get('actuator_id')}] {correction.get('label')}: "
            f"{correction.get('correction', 0.0):.2f}"
        )
    print(
        f"derivative_source={state.get('derivative_source')} "
        f"loop_rate_hz={_fmt_optional(state.get('loop_rate_hz'))}"
    )
    return 0


def cmd_preflight(base_url: str) -> int:
    results = run_preflight(base_url=base_url)
    print(format_results(results))
    return 1 if any(result.status == "NG" and result.required for result in results) else 0


def cmd_experiment_start(base_url: str, experiment_type: str, name: str | None) -> int:
    body: dict = {"experiment_type": experiment_type}
    if name:
        body["name"] = name
    manifest = api_post(base_url, "/api/experiments/start", body)
    git = manifest.get("git") or {}
    print(f"started experiment_id={manifest.get('experiment_id')}")
    print(f"type={manifest.get('experiment_type')} name={manifest.get('name') or '-'}")
    print(f"robot={manifest.get('robot')} sample_rate_hz={manifest.get('sample_rate_hz')}")
    print(f"git={git.get('branch')}@{git.get('sha')} dirty={git.get('dirty')}")
    return 0


def cmd_experiment_stop(base_url: str) -> int:
    summary = api_post(base_url, "/api/experiments/stop")
    manifest = summary.get("manifest") or {}
    print(f"stopped experiment_id={manifest.get('experiment_id')}")
    print(f"duration={human_duration(summary.get('duration_sec', 0.0))}")
    print(f"telemetry_rows={summary.get('telemetry_rows')} events={summary.get('event_count')}")
    print(f"telemetry_size={human_bytes(summary.get('telemetry_bytes', 0))}")
    print(f"directory={summary.get('directory')}")
    return 0


def cmd_experiment_list(base_url: str) -> int:
    experiments = api_get(base_url, "/api/experiments").get("experiments") or []
    if not experiments:
        print("(no experiments recorded yet)")
        return 0
    for manifest in experiments:
        row_counts = manifest.get("row_counts") or {}
        ended = manifest.get("ended_at")
        rows = row_counts.get("telemetry_rows", "-")
        print(
            f"{manifest.get('experiment_id')}  type={manifest.get('experiment_type')}  "
            f"started={manifest.get('started_at')}  ended={'yes' if ended else 'no'}  rows={rows}"
        )
    return 0


def _print_manifest(manifest: dict) -> None:
    git = manifest.get("git") or {}
    stab = manifest.get("stabilization") or {}
    gains = stab.get("gains") or {}
    row_counts = manifest.get("row_counts") or {}

    print(f"experiment_id: {manifest.get('experiment_id')}")
    print(f"type: {manifest.get('experiment_type')}  name: {manifest.get('name') or '-'}")
    print(f"robot: {manifest.get('robot')}  package_version: {manifest.get('package_version')}")
    print(f"git: {git.get('branch')}@{git.get('sha')} dirty={git.get('dirty')}")
    print(f"started_at: {manifest.get('started_at')}  ended_at: {manifest.get('ended_at') or '-'}")
    print(f"sample_rate_hz: {manifest.get('sample_rate_hz')}")
    print(
        f"stabilization.enabled: {stab.get('enabled')}  "
        f"derivative_source: {stab.get('derivative_source')}"
    )
    print(
        "gains: "
        f"kp_roll={gains.get('kp_roll')} ki_roll={gains.get('ki_roll')} "
        f"kd_roll={gains.get('kd_roll')} kp_pitch={gains.get('kp_pitch')} "
        f"ki_pitch={gains.get('ki_pitch')} kd_pitch={gains.get('kd_pitch')}"
    )
    print(f"row_counts: {row_counts or '-'}")


def cmd_experiment_show(base_url: str, experiment_id: str) -> int:
    if experiment_id == "latest":
        manifest = api_get(base_url, "/api/experiments/latest")
    else:
        experiments = api_get(base_url, "/api/experiments").get("experiments") or []
        manifest = next(
            (item for item in experiments if item.get("experiment_id") == experiment_id), None
        )
        if manifest is None:
            print(f"[NG] experiment not found: {experiment_id}")
            return 1
    _print_manifest(manifest)
    return 0


def cmd_experiment_note(base_url: str, text: str) -> int:
    result = api_post(base_url, "/api/experiments/note", {"text": text})
    print(f"note added at {result.get('ts')}: {result.get('text')}")
    return 0


# --------------------------------------------------------------------------
# argparse wiring
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="robotctl", description="CLI for the PQL-A00 control server HTTP API."
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"Control server host[:port] or URL (default: {DEFAULT_HOST})",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status", help="Show health + stabilization + sensors summary")
    subparsers.add_parser("sensors", help="Show IMU sensor readings")
    subparsers.add_parser("stabilization-status", help="Show stabilization controller state")
    subparsers.add_parser("preflight", help="Run local preflight checks")

    experiment_parser = subparsers.add_parser("experiment", help="Manage experiment logging runs")
    experiment_sub = experiment_parser.add_subparsers(dest="experiment_command", required=True)

    start_parser = experiment_sub.add_parser("start", help="Start a new experiment run")
    start_parser.add_argument("experiment_type", help="Experiment type/tag, e.g. smoke-test")
    start_parser.add_argument("--name", default=None, help="Optional human-readable run name")

    experiment_sub.add_parser("stop", help="Stop the running experiment")
    experiment_sub.add_parser("list", help="List recorded experiments (newest first)")

    show_parser = experiment_sub.add_parser("show", help="Show one experiment's manifest")
    show_parser.add_argument("experiment_id", help="Experiment id, or 'latest'")

    note_parser = experiment_sub.add_parser("note", help="Append a note to the running experiment")
    note_parser.add_argument("text", nargs="+", help="Note text (joined with spaces)")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    base_url = normalize_base_url(args.host)

    if args.command == "status":
        return cmd_status(base_url)
    if args.command == "sensors":
        return cmd_sensors(base_url)
    if args.command == "stabilization-status":
        return cmd_stabilization_status(base_url)
    if args.command == "preflight":
        return cmd_preflight(base_url)
    if args.command == "experiment":
        if args.experiment_command == "start":
            return cmd_experiment_start(base_url, args.experiment_type, args.name)
        if args.experiment_command == "stop":
            return cmd_experiment_stop(base_url)
        if args.experiment_command == "list":
            return cmd_experiment_list(base_url)
        if args.experiment_command == "show":
            return cmd_experiment_show(base_url, args.experiment_id)
        if args.experiment_command == "note":
            return cmd_experiment_note(base_url, " ".join(args.text))

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
