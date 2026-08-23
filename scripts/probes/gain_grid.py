"""Per-axis gain grid search: P x I combos scored on +/-300 step tracking.

Score = |final_error_up| + |final_error_down| + 1.5 * (tail_p2p_up + tail_p2p_down)
(lower is better: accurate AND quiet). Best gains are LEFT APPLIED per axis.
"""

from __future__ import annotations

import json
import time
import urllib.request

BASE = "http://127.0.0.1:8000"
NEUTRAL = 2048
STEP = 300
PHASE_S = 2.2
SAMPLE_S = 0.06
P_VALUES = [140, 200, 250]
I_VALUES = [0, 40, 80]
AXES = list(range(8))


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
    set_target(axis, target)
    t0 = time.monotonic()
    samples = []
    while time.monotonic() - t0 < PHASE_S:
        samples.append((round(time.monotonic() - t0, 3), position(axis)))
        time.sleep(SAMPLE_S)
    final = samples[-1][1]
    tail = [p for t, p in samples if t > PHASE_S - 1.0]
    return {"final_error": abs(target - final), "tail_p2p": max(tail) - min(tail)}


def combo_score(axis: int) -> dict:
    set_target(axis, NEUTRAL)
    time.sleep(1.5)
    up = phase(axis, NEUTRAL + STEP)
    down = phase(axis, NEUTRAL - STEP)
    set_target(axis, NEUTRAL)
    score = (
        up["final_error"]
        + down["final_error"]
        + 1.5 * (up["tail_p2p"] + down["tail_p2p"])
    )
    return {"up": up, "down": down, "score": round(score)}


def main() -> None:
    report: dict = {}
    for axis in AXES:
        label = api(f"/api/actuators/{axis}")["item"]["label"]
        results = []
        for p_gain in P_VALUES:
            for i_gain in I_VALUES:
                api(f"/api/actuators/{axis}/gain", {"p": p_gain, "i": i_gain, "d": 0})
                time.sleep(0.4)
                entry = {"p": p_gain, "i": i_gain, **combo_score(axis)}
                results.append(entry)
        best = min(results, key=lambda item: item["score"])
        api(f"/api/actuators/{axis}/gain", {"p": best["p"], "i": best["i"], "d": 0})
        report[axis] = {"label": label, "best": best, "all": results}
        print(f"# axis {axis} ({label}) best P={best['p']} I={best['i']} score={best['score']}", flush=True)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
