from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from serial.tools import list_ports


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BINDINGS_PATH = PROJECT_ROOT / ".highend_windows_ports.json"


@dataclass(slots=True)
class PortFingerprint:
    device: str
    serial_number: str | None
    location: str | None
    vid: int | None
    pid: int | None
    description: str | None
    manufacturer: str | None
    product: str | None
    interface: str | None
    hwid: str | None


@dataclass(slots=True)
class PortBindings:
    front: PortFingerprint
    back: PortFingerprint


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Detect and launch Highend Control Server with Front/Back COM bindings on Windows.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="List currently visible COM ports and their fingerprints.")

    bind_parser = subparsers.add_parser(
        "bind",
        help="Bind the current Front/Back COM ports and save a reusable mapping.",
    )
    bind_parser.add_argument("--front", required=True, help="Front ESP32 COM port, for example COM3.")
    bind_parser.add_argument("--back", required=True, help="Back ESP32 COM port, for example COM4.")
    bind_parser.add_argument(
        "--launch",
        action="store_true",
        help="Launch the real server immediately after saving the mapping.",
    )
    bind_parser.add_argument(
        "--demo",
        action="store_true",
        help="Launch in demo mode after saving the mapping.",
    )

    launch_parser = subparsers.add_parser(
        "launch",
        help="Resolve the saved Front/Back mapping against current COM ports and launch the server.",
    )
    launch_parser.add_argument(
        "--demo",
        action="store_true",
        help="Launch in demo mode instead of real serial mode.",
    )
    launch_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print the resolved Front/Back COM ports without starting the server.",
    )

    return parser.parse_args()


def normalize_device(device: str) -> str:
    return device.strip().upper()


def fingerprint_from_port_info(port) -> PortFingerprint:
    return PortFingerprint(
        device=normalize_device(port.device),
        serial_number=port.serial_number or None,
        location=port.location or None,
        vid=port.vid,
        pid=port.pid,
        description=port.description or None,
        manufacturer=port.manufacturer or None,
        product=getattr(port, "product", None) or None,
        interface=getattr(port, "interface", None) or None,
        hwid=port.hwid or None,
    )


def enumerate_ports() -> dict[str, PortFingerprint]:
    ports: dict[str, PortFingerprint] = {}
    for port in list_ports.comports():
        fingerprint = fingerprint_from_port_info(port)
        ports[fingerprint.device] = fingerprint
    return ports


def load_bindings() -> PortBindings | None:
    if not BINDINGS_PATH.exists():
        return None

    raw = json.loads(BINDINGS_PATH.read_text(encoding="utf-8"))
    return PortBindings(
        front=PortFingerprint(**raw["front"]),
        back=PortFingerprint(**raw["back"]),
    )


def save_bindings(bindings: PortBindings) -> None:
    BINDINGS_PATH.write_text(
        json.dumps(
            {
                "front": asdict(bindings.front),
                "back": asdict(bindings.back),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def score_binding(binding: PortFingerprint, candidate: PortFingerprint) -> int:
    score = 0
    if binding.serial_number and binding.serial_number == candidate.serial_number:
        score += 100
    if binding.location and binding.location == candidate.location:
        score += 40
    if binding.vid is not None and binding.vid == candidate.vid:
        score += 10
    if binding.pid is not None and binding.pid == candidate.pid:
        score += 10
    if binding.hwid and binding.hwid == candidate.hwid:
        score += 10
    if binding.device == candidate.device:
        score += 5
    return score


def resolve_binding(binding: PortFingerprint, ports: dict[str, PortFingerprint]) -> PortFingerprint | None:
    if binding.device in ports:
        exact = ports[binding.device]
        if score_binding(binding, exact) > 0:
            return exact

    ranked = sorted(
        ((score_binding(binding, candidate), candidate) for candidate in ports.values()),
        key=lambda item: item[0],
        reverse=True,
    )
    if not ranked or ranked[0][0] <= 0:
        return None
    return ranked[0][1]


def ensure_bound_device(device: str, ports: dict[str, PortFingerprint], role: str) -> PortFingerprint:
    normalized = normalize_device(device)
    try:
        return ports[normalized]
    except KeyError as exc:
        visible = ", ".join(sorted(ports)) or "none"
        raise SystemExit(f"{role} port {normalized} is not visible. Current COM ports: {visible}") from exc


def print_ports(ports: dict[str, PortFingerprint]) -> None:
    if not ports:
        print("No COM ports detected.")
        return

    print("Detected COM ports:")
    for device in sorted(ports):
        port = ports[device]
        print(
            f"- {port.device}: serial={port.serial_number or '-'} "
            f"location={port.location or '-'} vid={port.vid} pid={port.pid} "
            f"description={port.description or '-'}"
        )


def launch_server(front: str | None, back: str | None, demo: bool) -> int:
    env = os.environ.copy()
    if front:
        env["HIGHEND_FRONT_PORT_NAME"] = front
    if back:
        env["HIGHEND_BACK_PORT_NAME"] = back
    if demo:
        env["HIGHEND_EMULATE_DEVICES"] = "true"
    else:
        env.pop("HIGHEND_EMULATE_DEVICES", None)

    command = [sys.executable, "-m", "highend_server"]
    return subprocess.call(command, cwd=str(PROJECT_ROOT), env=env)


def handle_bind(args: argparse.Namespace) -> int:
    ports = enumerate_ports()
    front = ensure_bound_device(args.front, ports, "Front")
    back = ensure_bound_device(args.back, ports, "Back")
    if front.device == back.device:
        raise SystemExit("Front and Back must be different COM ports.")

    bindings = PortBindings(front=front, back=back)
    save_bindings(bindings)
    print(f"Saved Windows COM bindings to {BINDINGS_PATH}")
    print(f"Front -> {front.device}")
    print(f"Back  -> {back.device}")

    if args.launch:
        return launch_server(front.device, back.device, args.demo)
    return 0


def handle_launch(args: argparse.Namespace) -> int:
    bindings = load_bindings()
    if bindings is None:
        raise SystemExit(
            "No Windows COM bindings saved yet. Run:\n"
            "  python scripts/detect_windows_ports.py list\n"
            "  python scripts/detect_windows_ports.py bind --front COMx --back COMy"
        )

    ports = enumerate_ports()
    front = resolve_binding(bindings.front, ports)
    back = resolve_binding(bindings.back, ports)

    if front is None or back is None:
        print_ports(ports)
        missing = []
        if front is None:
            missing.append("Front")
        if back is None:
            missing.append("Back")
        raise SystemExit(
            f"Could not resolve saved binding for {', '.join(missing)}. "
            "Reconnect the devices or run bind again."
        )

    if front.device == back.device:
        raise SystemExit(
            f"Resolved both Front and Back to {front.device}. "
            "Run bind again so each ESP32 is stored with a distinct fingerprint."
        )

    print(f"Resolved Front -> {front.device}")
    print(f"Resolved Back  -> {back.device}")
    if args.dry_run:
        return 0
    return launch_server(front.device, back.device, args.demo)


def main() -> int:
    args = parse_args()

    if args.command == "list":
        print_ports(enumerate_ports())
        return 0
    if args.command == "bind":
        return handle_bind(args)
    if args.command == "launch":
        return handle_launch(args)

    raise SystemExit(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
