"""Position-jump noise: walking vs idle (idle jumps = pure sensor noise)."""

import csv
import statistics
import sys
from pathlib import Path

RUN = sys.argv[1]
BASE = Path("/home/rabbot/pql-webapp/Logs/experiments") / RUN


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


rows = list(csv.DictReader((BASE / "telemetry.csv").open(encoding="utf-8")))
labels = ["FR hip", "FR knee", "FL hip", "FL knee", "RR hip", "RR knee", "RL hip", "RL knee"]


def jumps(sel):
    out = []
    for a, b in zip(sel, sel[1:], strict=False):
        dt = (float(b["elapsed_ms"]) - float(a["elapsed_ms"])) / 1000
        if not 0.02 < dt < 0.2:
            continue
        va, vb = num(a["actual_position"]), num(b["actual_position"])
        if None in (va, vb):
            continue
        out.append(abs(vb - va))
    return sorted(out)


print(" ax label      WALKING med/p95/max      IDLE med/p95/max")
for ax in range(8):
    line = f"{ax:3d} {labels[ax]:<9} "
    for want in ("1", "0"):
        sel = sorted((r for r in rows
                      if r["actuator_id"] == str(ax) and r.get("walk_active") == want),
                     key=lambda r: float(r["elapsed_ms"]))
        j = jumps(sel) if len(sel) > 50 else []
        if j:
            line += f"{statistics.median(j):5.0f}/{j[int(len(j)*0.95)]:5.0f}/{max(j):6.0f}    "
        else:
            line += "     (no data)        "
    print(line)
print()
print("A cylinder measured at 344-819 units/s can move at most ~33 units per 40 ms tick.")
print("Jumps far above that are electrical/ADC noise, not motion.")

