"""Full-stroke bang-bang probe: target 0 <-> 4095, 5 reps per axis.

Runs on the Pi against localhost. Measures per step: start/final position,
movement onset delay, t63 over the remaining span, mean velocity, command
extremes. Ends every axis back at 2048.
"""

from __future__ import annotations

import json
import time
import urllib.request

BASE = "http://127.0.0.1:8000"
LOW = 0
HIGH = 4095
NEUTRAL = 2048
REPS = 5
STEP_S = 3.0
SAMPLE_S = 0.06
AXES = list(range(8))


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


def step(axis: int, target: int) -> dict:
    start_pos = get_axis(axis)["telemetry"]["position"]
    set_target(axis, target)
    t0 = time.monotonic()
    samples = []
    while time.monotonic() - t0 < STEP_S:
        item = get_axis(axis)
        samples.append(
            (
                round(time.monotonic() - t0, 3),
                item["telemetry"]["position"],
                item["telemetry"]["command"],
            )
        )
        time.sleep(SAMPLE_S)
    span = target - start_pos
    onset = None
    t63 = None
    for t, pos, _ in samples:
        if onset is None and abs(pos - start_pos) > 60:
            onset = t
        if span and t63 is None:
            if (span > 0 and pos >= start_pos + 0.63 * span) or (
                span < 0 and pos <= start_pos + 0.63 * span
            ):
                t63 = t
    final = samples[-1][1]
    velocity = None
    if onset is not None:
        travel = abs(final - start_pos)
        duration = samples[-1][0] - onset
        if duration > 0.1:
            velocity = round(travel / duration)
    commands = [c for _, _, c in samples]
    return {
        "start": start_pos,
        "final": final,
        "travel": final - start_pos,
        "span": span,
        "progress": round((final - start_pos) / span, 2) if span else None,
        "onset_s": onset,
        "t63_s": t63,
        "vel_ups": velocity,
        "cmd_min": min(commands),
        "cmd_max": max(commands),
    }


def main() -> None:
    report: dict = {}
    for axis in AXES:
        label = get_axis(axis)["label"]
        set_target(axis, LOW)
        time.sleep(STEP_S)  # pre-position at LOW (not measured)
        ups, downs = [], []
        for _ in range(REPS):
            ups.append(step(axis, HIGH))
            downs.append(step(axis, LOW))
        set_target(axis, NEUTRAL)
        report[axis] = {"label": label, "up_0_to_4095": ups, "down_4095_to_0": downs}
        print(f"# axis {axis} ({label}) done", flush=True)
        time.sleep(1.0)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
