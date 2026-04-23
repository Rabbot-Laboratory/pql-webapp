from __future__ import annotations

import argparse
import os


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start the Highend Control Server.")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Start in demo mode with emulated ESP32 devices instead of serial ports.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.demo:
        os.environ["HIGHEND_EMULATE_DEVICES"] = "true"

    from highend_server.main import run

    run()


if __name__ == "__main__":
    main()
