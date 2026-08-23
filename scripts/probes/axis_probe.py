"""Suspended-robot axis probe: absolute step tests around neutral (2048).

Runs on the Pi against localhost. For each axis: 2048 (settle) -> +300 ->
-300 -> back to 2048, sampling position/command at ~12 Hz. Prints a JSON
report to stdout.
"""

from __future__ import annotations

import json
import time
import urllib.request

BASE = "http://127.0.0.1:8000"
NEUTRAL = 2048
STEP = 300
SETTLE_S = 3.0
SAMPLE_S = 0.08
AXES = [6, 0, 1, 2, 3, 4, 5, 7]  # suspects first


def get_axis(axis: int) -> dict:
    with urllib.request.urlopen(f"{BASE}/api/actuators/{axis}", timeout=5) as resp:
        return json.load(resp)["item"]


def set_target(axis: int, value: int) -> None:
    body = json.dumps({"mode": "position", "value": int(value)}).encode()
    req = urllib.request.Request(
        f"{BASE}/api/actuators/{axis}/target",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(req, timeout=5).read()


def sample_phase(axis: int, target: int, seconds: float) -> list[dict]:
    set_target(axis, target)
    samples = []
    start = time.monotonic()
    while time.monotonic() - start < seconds:
        item = get_axis(axis)
        samples.append(
            {
                "t": round(time.monotonic() - start, 3),
                "pos": item["telemetry"]["position"],
                "cmd": item["telemetry"]["command"],
            }
        )
        time.sleep(SAMPLE_S)
    return samples


def analyse(samples: list[dict], start_pos: int, target: int) -> dict:
    positions = [s["pos"] for s in samples]
    commands = [s["cmd"] for s in samples]
    span = target - start_pos
    moved = max(positions) - min(positions)
    final = positions[-1]
    progress = (final - start_pos) / span if span else None
    # time to reach 63% of the step
    t63 = None
    if span:
        threshold = start_pos + span * 0.63
        for s in samples:
            if (span > 0 and s["pos"] >= threshold) or (span < 0 and s["pos"] <= threshold):
                t63 = s["t"]
                break
    return {
        "target": target,
        "start": start_pos,
        "final": final,
        "final_error": target - final,
        "moved_range": moved,
        "progress": None if progress is None else round(progress, 2),
        "t63_s": t63,
        "cmd_min": min(commands),
        "cmd_max": max(commands),
        "cmd_final": commands[-1],
    }


def main() -> None:
    report = {}
    for axis in AXES:
        item = get_axis(axis)
        label = item["label"]
        # settle at neutral first
        neutral_samples = sample_phase(axis, NEUTRAL, SETTLE_S)
        pos0 = neutral_samples[-1]["pos"]
        up = analyse(sample_phase(axis, NEUTRAL + STEP, SETTLE_S), pos0, NEUTRAL + STEP)
        pos1 = up["final"]
        down = analyse(sample_phase(axis, NEUTRAL - STEP, SETTLE_S), pos1, NEUTRAL - STEP)
        back = sample_phase(axis, NEUTRAL, SETTLE_S)
        report[axis] = {
            "label": label,
            "neutral_reached": neutral_samples[-1]["pos"],
            "neutral_cmd": neutral_samples[-1]["cmd"],
            "extend": up,
            "contract": down,
            "returned_to": back[-1]["pos"],
        }
        print(f"# axis {axis} ({label}) done", flush=True)
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
