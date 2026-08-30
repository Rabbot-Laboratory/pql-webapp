"""Compare walking segments (per gait) inside one run."""

import csv
import statistics
import sys
from pathlib import Path

RUN = sys.argv[1]
# name:start:end triples in seconds
SEGMENTS = [tuple(s.split(":")) for s in sys.argv[2:]]
BASE = Path("/home/rabbot/pql-webapp/Logs/experiments") / RUN


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


rows = [r for r in csv.DictReader((BASE / "telemetry.csv").open(encoding="utf-8"))
        if r.get("walk_active") == "1"]
labels = ["FR hip", "FR knee", "FL hip", "FL knee", "RR hip", "RR knee", "RL hip", "RL knee"]

for name, start, end in SEGMENTS:
    lo, hi = float(start) * 1000, float(end) * 1000
    seg = [r for r in rows if lo <= float(r["elapsed_ms"]) <= hi]
    if not seg:
        print(f"\n=== {name}: no samples")
        continue
    ticks = {}
    for r in seg:
        ticks.setdefault(r["elapsed_ms"], r)
    rolls = [num(t["control_roll"]) or 0.0 for t in ticks.values()]
    pitches = [num(t["control_pitch"]) or 0.0 for t in ticks.values()]
    dur = (hi - lo) / 1000
    print(f"\n=== {name}  ({dur:.0f}s)  roll {min(rolls):+.1f}..{max(rolls):+.1f}  "
          f"pitch {min(pitches):+.1f}..{max(pitches):+.1f}")
    print("  ax label      cmd_amp act_amp ratio  mean|err|  end%   move/s")
    tot_move = 0.0
    for ax in range(8):
        sel = sorted((r for r in seg if r["actuator_id"] == str(ax)),
                     key=lambda r: float(r["elapsed_ms"]))
        if len(sel) < 20:
            continue
        tg = [v for v in (num(r["effective_target"]) for r in sel) if v is not None]
        ac = [v for v in (num(r["actual_position"]) for r in sel) if v is not None]
        err = [abs(num(r["effective_target"]) - num(r["actual_position"])) for r in sel
               if num(r["effective_target"]) is not None and num(r["actual_position"]) is not None]
        # total travel of the actual position per second: how much the axis
        # actually works, independent of whether it tracked the command
        travel = sum(abs(b - a) for a, b in zip(ac, ac[1:], strict=False))
        ends = sum(1 for a in ac if a <= 10 or a >= 4085) / len(ac) * 100
        cmd_amp = (max(tg) - min(tg)) / 2
        act_amp = (max(ac) - min(ac)) / 2
        tot_move += travel / dur
        print(f"  {ax:2d} {labels[ax]:<9} {cmd_amp:7.0f} {act_amp:7.0f} "
              f"{act_amp/cmd_amp if cmd_amp else 0:5.2f} {statistics.mean(err):10.0f} "
              f"{ends:5.1f} {travel/dur:8.0f}")
    print(f"  total actual travel across axes: {tot_move:.0f} units/s")
