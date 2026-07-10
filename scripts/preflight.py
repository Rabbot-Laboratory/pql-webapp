"""Preflight checks for the PQL-A00 robot controller.

Stdlib-only (no third-party imports) so this script can run on a bare
Raspberry Pi before the project virtualenv or any dependencies exist. Each
check is a small, pure/injectable function returning a `CheckResult`; hardware
checks that fail on a dev machine (no I2C bus, no serial adapters, smbus2 not
installed) are expected and are not treated as script errors.

Usage::

    python scripts/preflight.py [--host 127.0.0.1:8000]

Also importable from ``scripts/robotctl.py`` (the ``preflight`` subcommand).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

DEFAULT_HOST = "127.0.0.1:8000"


def normalize_base_url(host: str) -> str:
    """Turn a bare `host:port` (or full URL) into a usable base URL."""
    host = host.strip()
    if "://" in host:
        return host.rstrip("/")
    return f"http://{host}".rstrip("/")


@dataclass
class CheckResult:
    name: str
    status: str  # "OK" | "NG" | "SKIP"
    detail: str
    required: bool = True


def check_python(min_version: tuple[int, int] = (3, 11)) -> CheckResult:
    current = sys.version_info[:2]
    version_str = ".".join(str(part) for part in sys.version_info[:3])
    if current >= min_version:
        return CheckResult("Python", "OK", version_str)
    required_str = f"{min_version[0]}.{min_version[1]}"
    return CheckResult("Python", "NG", f"{version_str} < required {required_str}")


def check_git(project_root: Path) -> CheckResult:
    def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(project_root), *args],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )

    try:
        branch = _run(["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
        sha = _run(["rev-parse", "--short", "HEAD"]).stdout.strip()
        porcelain = _run(["status", "--porcelain"]).stdout
    except (OSError, subprocess.SubprocessError) as error:
        return CheckResult("Git", "NG", f"not a git repo or git unavailable: {error}")

    state = "dirty" if porcelain.strip() else "clean"
    return CheckResult("Git", "OK", f"{branch}@{sha} ({state})")


def check_i2c(dev_path: str = "/dev/i2c-1") -> CheckResult:
    path = Path(dev_path)
    if path.exists():
        return CheckResult("I2C bus", "OK", str(path))
    return CheckResult("I2C bus", "NG", f"{path} not found")


def check_bmx055(
    bus: int = 1, addresses: tuple[int, ...] = (0x18, 0x68, 0x10)
) -> CheckResult:
    try:
        import smbus2
    except ImportError:
        return CheckResult("BMX055", "SKIP", "smbus2 not installed — run on Pi")

    try:
        handle = smbus2.SMBus(bus)
    except OSError as error:
        return CheckResult("BMX055", "NG", f"cannot open i2c bus {bus}: {error}")

    missing: list[str] = []
    try:
        for address in addresses:
            try:
                handle.read_byte(address)
            except OSError:
                missing.append(hex(address))
    finally:
        handle.close()

    if missing:
        return CheckResult("BMX055", "NG", f"not detected at {', '.join(missing)}")
    detected = ", ".join(hex(address) for address in addresses)
    return CheckResult("BMX055", "OK", f"responded at {detected}")


def check_serial_ports(
    paths: tuple[str, ...] = ("/dev/ttyUSB-Front", "/dev/ttyUSB-Back"),
) -> CheckResult:
    missing = [path for path in paths if not Path(path).exists()]
    if missing:
        return CheckResult("Serial ports", "NG", f"missing: {', '.join(missing)}")
    return CheckResult("Serial ports", "OK", ", ".join(paths))


def check_api_health(base_url: str, timeout: float = 2.0) -> CheckResult:
    url = normalize_base_url(base_url) + "/api/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            body = response.read().decode("utf-8")
    except (urllib.error.URLError, OSError, ValueError) as error:
        return CheckResult(
            "API health", "NG", f"{url} unreachable: {error}", required=False
        )

    try:
        data = json.loads(body)
    except json.JSONDecodeError as error:
        return CheckResult(
            "API health", "NG", f"invalid JSON from {url}: {error}", required=False
        )

    ok = bool(data.get("ok"))
    service = data.get("service", "?")
    system = data.get("system") or {}
    detail = (
        f"{service} ok={ok} state={system.get('connection_state')} "
        f"emulate={system.get('emulate_devices')}"
    )
    return CheckResult("API health", "OK" if ok else "NG", detail, required=False)


def check_logs_writable(logs_dir: Path) -> CheckResult:
    logs_dir = Path(logs_dir)
    probe = logs_dir / ".preflight_probe"
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        probe.touch()
        probe.unlink()
    except OSError as error:
        return CheckResult("Logs writable", "NG", f"{logs_dir}: {error}")
    return CheckResult("Logs writable", "OK", str(logs_dir))


def check_disk_free(path: Path, min_free_gb: float = 1.0) -> CheckResult:
    path = Path(path)
    probe_path = path if path.exists() else path.parent
    usage = shutil.disk_usage(probe_path)
    free_gb = usage.free / (1024**3)
    detail = f"{free_gb:.1f} GB free at {path}"
    if free_gb < min_free_gb:
        return CheckResult("Disk free", "NG", detail)
    return CheckResult("Disk free", "OK", detail)


def run_preflight(
    base_url: str = "http://127.0.0.1:8000", project_root: Path | None = None
) -> list[CheckResult]:
    if project_root is None:
        project_root = Path(__file__).resolve().parents[1]
    else:
        project_root = Path(project_root)
    logs_dir = project_root / "Logs"
    return [
        check_python(),
        check_git(project_root),
        check_i2c(),
        check_bmx055(),
        check_serial_ports(),
        check_api_health(base_url),
        check_logs_writable(logs_dir),
        check_disk_free(project_root),
    ]


def format_results(results: list[CheckResult]) -> str:
    return "\n".join(f"[{result.status}] {result.name}: {result.detail}" for result in results)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run PQL-A00 preflight checks.")
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"Control server host[:port] or URL (default: {DEFAULT_HOST})",
    )
    args = parser.parse_args(argv)

    base_url = normalize_base_url(args.host)
    results = run_preflight(base_url=base_url)
    print(format_results(results))

    if any(result.status == "NG" and result.required for result in results):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
