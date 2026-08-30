"""Per-run walking analysis: event timeline + per-axis tracking during walk."""

import csv
import json
import sys
from pathlib import Path

RUN = sys.argv[1]
BASE = Path("/home/rabbot/pql-webapp/Logs/experiments") / RUN


def num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


print(f"===== {RUN}")
events = BASE / "events.jsonl"
if events.exists():
    last = None
    for line in events.open(encoding="utf-8"):
        e = json.loads(line)
        t, p = e.get("type"), e.get("payload") or {}
        secs = e.get("elapsed_ms", 0) / 1000
        if t == "adaptive_walk_state":
            w = p.get("adaptive_walk") or {}
            key = (w.get("active"), w.get("motion_name"), w.get("mode"), w.get("stopped_reason"))
            if key != last:
                last = key
                print(
                    f"{secs:7.1f}s WALK active={key[0]} motion={key[1]} "
                    f"mode={key[2]} reason={key[3]}"
                )

rows = list(csv.DictReader((BASE / "telemetry.csv").open(encoding="utf-8")))
walking = [r for r in rows if r.get("walk_active") == "1"]
if not walking:
    print("(no walking samples)")
    sys.exit()

start = min(float(r["elapsed_ms"]) for r in walking) / 1000
end = max(float(r["elapsed_ms"]) for r in walking) / 1000
print(f"\nwalking window: {start:.1f}s - {end:.1f}s ({end - start:.1f}s)")

ticks = {}
for r in walking:
    ticks.setdefault(r["elapsed_ms"], r)
rolls = [num(t["control_roll"]) or 0.0 for t in ticks.values()]
pitches = [num(t["control_pitch"]) or 0.0 for t in ticks.values()]
print(f"attitude during walk: roll {min(rolls):+.1f}..{max(rolls):+.1f}  "
      f"pitch {min(pitches):+.1f}..{max(pitches):+.1f}")

labels = ["FR hip", "FR knee", "FL hip", "FL knee", "RR hip", "RR knee", "RL hip", "RL knee"]
print("\n ax label      cmd_amp  act_amp  ratio   mean|err|  end%  ratelim%")
for ax in range(8):
    sel = [r for r in walking if r["actuator_id"] == str(ax)]
    if not sel:
        continue
    tg = [v for v in (num(r["effective_target"]) for r in sel) if v is not None]
    ac = [v for v in (num(r["actual_position"]) for r in sel) if v is not None]
    if not tg or not ac:
        continue
    cmd_amp = (max(tg) - min(tg)) / 2
    act_amp = (max(ac) - min(ac)) / 2
    err = [abs(num(r["effective_target"]) - num(r["actual_position"])) for r in sel
           if num(r["effective_target"]) is not None and num(r["actual_position"]) is not None]
    ends = sum(1 for a in ac if a <= 10 or a >= 4085) / len(ac) * 100
    rl = sum(1 for r in sel if r.get("walk_rate_limited") == "1") / len(sel) * 100
    ratio = act_amp / cmd_amp if cmd_amp else 0
    print(f"{ax:3d} {labels[ax]:<9} {cmd_amp:7.0f} {act_amp:8.0f} {ratio:6.2f} "
          f"{sum(err)/len(err):10.0f} {ends:5.1f} {rl:9.1f}")
