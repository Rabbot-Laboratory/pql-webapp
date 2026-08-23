"""Gain tuning probe: baseline vs P=250/I=60 on +/-300 steps, per axis.

Runs on the Pi. For each axis: read gains -> +/-300 step test -> set new
gains -> repeat test -> report both (new gains are left applied; not saved
to EEPROM). Flags oscillation via peak-to-peak in the last 1.2 s of a phase.
"""

from __future__ import annotations

import json
import time
import urllib.request

BASE = "http://127.0.0.1:8000"
NEUTRAL = 2048
STEP = 300
PHASE_S = 2.5
SAMPLE_S = 0.06
NEW_P = 250
NEW_I = 60
AXES = [0, 5, 6, 4, 2, 7, 3, 1]

ORIGINAL_GAINS = {
    0: {"p": 190, "i": 90, "d": 0},
    1: {"p": 150, "i": 180, "d": 0},
    2: {"p": 180, "i": 160, "d": 0},
    3: {"p": 170, "i": 200, "d": 0},
    4: {"p": 180, "i": 0, "d": 0},
    5: {"p": 180, "i": 0, "d": 0},
    6: {"p": 180, "i": 0, "d": 0},
    7: {"p": 180, "i": 0, "d": 0},
}



def api(path: str, body: dict | None = None) -> dict:
    if body is None:
        with urllib.request.urlopen(f"{BASE}{path}", timeout=5) as resp:
            return json.load(resp)
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.load(resp)


def position(axis: int) -> int:
    return api(f"/api/actuators/{axis}")["item"]["telemetry"]["position"]


def set_target(axis: int, value: int) -> None:
    api(f"/api/actuators/{axis}/target", {"mode": "position", "value": int(value)})


def phase(axis: int, target: int) -> dict:
    start_pos = position(axis)
    set_target(axis, target)
    t0 = time.monotonic()
    samples = []
    while time.monotonic() - t0 < PHASE_S:
        samples.append((round(time.monotonic() - t0, 3), position(axis)))
        time.sleep(SAMPLE_S)
    span = target - start_pos
    final = samples[-1][1]
    t63 = None
    if span:
        for t, pos in samples:
            if (span > 0 and pos >= start_pos + 0.63 * span) or (
                span < 0 and pos <= start_pos + 0.63 * span
            ):
                t63 = t
                break
    tail = [pos for t, pos in samples if t > PHASE_S - 1.2]
    return {
        "span": span,
        "progress": round((final - start_pos) / span, 2) if span else None,
        "t63_s": t63,
        "final_error": target - final,
        "tail_p2p": max(tail) - min(tail),
    }


def test_axis(axis: int) -> dict:
    set_target(axis, NEUTRAL)
    time.sleep(2.0)
    up = phase(axis, NEUTRAL + STEP)
    down = phase(axis, NEUTRAL - STEP)
    set_target(axis, NEUTRAL)
    time.sleep(1.0)
    return {"up": up, "down": down}


def main() -> None:
    report: dict = {}
    for axis in AXES:
        item = api(f"/api/actuators/{axis}")["item"]
        gains = ORIGINAL_GAINS[axis]
        api(f"/api/actuators/{axis}/gain", gains)  # restore pre-probe gains
        time.sleep(0.5)
        baseline = test_axis(axis)
        api(
            f"/api/actuators/{axis}/gain",
            {"p": NEW_P, "i": NEW_I, "d": gains["d"]},
        )
        time.sleep(0.5)
        tuned = test_axis(axis)
        report[axis] = {
            "label": item["label"],
            "old_gains": gains,
            "new_gains": {"p": NEW_P, "i": NEW_I, "d": gains["d"]},
            "baseline": baseline,
            "tuned": tuned,
        }
        print(f"# axis {axis} ({item['label']}) done", flush=True)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
