"""Command-line entry point for the PQL-A00 MuJoCo simulation."""

from __future__ import annotations

import argparse
from pathlib import Path

from highend_server.simulation.config import load_simulation_config
from highend_server.simulation.runner import PqlA00Simulation


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the PQL-A00 pneumatic walking simulation")
    parser.add_argument("--duration", type=float, default=12.0, help="simulation duration [s]")
    parser.add_argument(
        "--headless", action="store_true", help="run without the interactive viewer"
    )
    parser.add_argument(
        "--no-adaptation", action="store_true", help="disable online phase-lead learning"
    )
    parser.add_argument(
        "--no-imu-control",
        action="store_true",
        help="disable IMU body-level feedback and adaptive gait trim",
    )
    parser.add_argument("--config", type=Path, help="pneumatic characteristic JSON")
    parser.add_argument("--model", type=Path, help="override the MJCF model path")
    parser.add_argument("--log", type=Path, help="write simulation telemetry CSV")
    parser.add_argument("--quiet", action="store_true", help="hide one-second progress reports")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_simulation_config(args.config)
    simulation = PqlA00Simulation(
        config,
        model_path=args.model,
        adaptive=not args.no_adaptation,
        imu_control=not args.no_imu_control,
    )
    result = simulation.run(
        args.duration,
        viewer=not args.headless,
        log_path=args.log,
        quiet=args.quiet,
    )
    print("\nSimulation result")
    print(f"  forward distance : {result.forward_distance_m:+.3f} m")
    print(f"  lateral drift    : {result.lateral_drift_m:+.3f} m")
    print(f"  final height     : {result.final_base_height_m:.3f} m")
    print(
        f"  max roll / pitch : {result.max_abs_roll_deg:.1f} / "
        f"{result.max_abs_pitch_deg:.1f} deg"
    )
    print(
        f"  mean |roll/pitch|: {result.mean_abs_roll_deg:.2f} / "
        f"{result.mean_abs_pitch_deg:.2f} deg"
    )
    print(f"  mean joint error : {result.mean_tracking_error_deg:.2f} deg")
    print(
        f"  learned IMU trim : {result.learned_roll_trim_deg:+.2f} / "
        f"{result.learned_pitch_trim_deg:+.2f} deg"
    )
    print(f"  fallen           : {'YES' if result.fallen else 'no'}")
    print("  learned lead [s] :")
    for name, lead_s in result.learned_lead_s.items():
        print(f"    {name:8s} {lead_s:.3f}")
    return 2 if result.fallen else 0


if __name__ == "__main__":
    raise SystemExit(main())
