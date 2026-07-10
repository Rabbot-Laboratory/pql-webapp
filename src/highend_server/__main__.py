from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start the Highend Control Server.")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Start in demo mode with emulated ESP32 devices instead of serial ports.",
    )
    parser.add_argument(
        "--replay",
        metavar="EXPERIMENT_DIR",
        type=Path,
        default=None,
        help=(
            "Replay a recorded experiment's telemetry.csv through the live IMU "
            "pipeline (Mahony filter) instead of real/emulated hardware. "
            "EXPERIMENT_DIR is an experiment directory containing telemetry.csv "
            "(e.g. Logs/experiments/<id>/). Forces HIGHEND_EMULATE_DEVICES=true "
            "(serial ports stay stubbed). PITFALL: the CSV stores "
            "bias-corrected gyro and calibrated mag, so unless "
            "HIGHEND_SENSOR_CONFIG_DIR_NAME is already set in the environment, "
            "this also points it at a fresh empty temp directory — otherwise a "
            "non-empty config/imu_calibration.json would apply those "
            "corrections a second time on top of the already-corrected "
            "recorded values."
        ),
    )
    parser.add_argument(
        "--replay-speed",
        metavar="FLOAT",
        type=float,
        default=None,
        help=(
            "Playback speed multiplier for --replay (>1 replays faster than "
            "real time, <1 slower). Sets HIGHEND_REPLAY_TIME_SCALE. Ignored "
            "without --replay."
        ),
    )
    args = parser.parse_args()

    if args.replay is not None:
        replay_dir = args.replay.resolve()
        if not (replay_dir / "telemetry.csv").exists():
            parser.error(
                f"--replay directory {replay_dir} has no telemetry.csv "
                "(expected an experiment directory, e.g. Logs/experiments/<id>/)"
            )
        args.replay = replay_dir

    return args


def main() -> None:
    args = parse_args()
    if args.demo:
        os.environ["HIGHEND_EMULATE_DEVICES"] = "true"

    if args.replay is not None:
        os.environ["HIGHEND_REPLAY_DIR"] = str(args.replay)
        # Serial ports stay stubbed for a replay run: only the IMU source is
        # swapped, but SensorService's real-vs-emulated ADC/device wiring is
        # driven by the same emulate_devices flag.
        os.environ["HIGHEND_EMULATE_DEVICES"] = "true"
        if "HIGHEND_SENSOR_CONFIG_DIR_NAME" not in os.environ:
            # See the --replay help text: without this, a non-empty
            # config/imu_calibration.json would double-apply gyro/mag
            # corrections that are already baked into the recorded CSV.
            os.environ["HIGHEND_SENSOR_CONFIG_DIR_NAME"] = tempfile.mkdtemp(
                prefix="replay-config-"
            )

    if args.replay_speed is not None:
        os.environ["HIGHEND_REPLAY_TIME_SCALE"] = str(args.replay_speed)

    from highend_server.main import run

    run()


if __name__ == "__main__":
    main()
