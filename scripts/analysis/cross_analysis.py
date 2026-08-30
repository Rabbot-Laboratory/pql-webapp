"""Cross-run analysis of every walking segment recorded today.

For each segment (one motion, one press) computes metrics that might explain
which gaits actually carried the robot:
  * travel/s   - how much the axes moved in total (raw activity)
  * coherence  - how periodic the body attitude was at the gait frequency;
                 real walking shifts weight once per cycle, vibration does not
  * pitch/roll oscillation amplitude at that frequency
  * contact duty per leg (if the ADC has data)
  * end%       - axes pinned at a mechanical end (wasted stroke)
"""

import csv
import json
import math
import statistics
import sys
from pathlib import Path

ROOT = Path("/home/rabbot/pql-webapp/Logs/experiments")
RUNS = sys.argv[1:]


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def segments(run):
    """(motion, mode, start_s, end_s) for each contiguous walking press."""
    path = ROOT / run / "events.jsonl"
    if not path.exists():
        return []
    out, active = [], None
    for line in path.open(encoding="utf-8"):
        e = json.loads(line)
        if e.get("type") != "adaptive_walk_state":
            continue
        w = (e.get("payload") or {}).get("adaptive_walk") or {}
        secs = e.get("elapsed_ms", 0) / 1000
        if w.get("active") and active is None:
            active = (w.get("motion_name"), w.get("mode"), secs)
        elif not w.get("active") and active is not None:
            if secs - active[2] >= 8.0:  # ignore accidental taps
                out.append((*active, secs))
            active = None
    return out


def analyse(run, motion, mode, start, end):
    rows = [r for r in csv.DictReader((ROOT / run / "telemetry.csv").open(encoding="utf-8"))
            if r.get("walk_active") == "1"
            and start * 1000 <= float(r["elapsed_ms"]) <= end * 1000]
    if len(rows) < 100:
        return None
    ticks = {}
    for r in rows:
        ticks.setdefault(r["elapsed_ms"], r)
    ordered = sorted(ticks.values(), key=lambda r: float(r["elapsed_ms"]))
    dur = end - start

    travel = 0.0
    pinned = 0.0
    axes_used = 0
    for ax in range(8):
        sel = sorted((r for r in rows if r["actuator_id"] == str(ax)),
                     key=lambda r: float(r["elapsed_ms"]))
        ac = [v for v in (num(r["actual_position"]) for r in sel) if v is not None]
        if len(ac) < 20:
            continue
        axes_used += 1
        travel += sum(abs(b - a) for a, b in zip(ac, ac[1:], strict=False))
        pinned += sum(1 for a in ac if a <= 10 or a >= 4085) / len(ac)

    # attitude coherence at the gait frequency, from walk_phase wraps
    phases = [(float(r["elapsed_ms"]) / 1000, num(r.get("walk_phase"))) for r in ordered]
    phases = [(t, p) for t, p in phases if p is not None and p >= 0]
    cycle = None
    wraps = [t for (t0, p0), (t, p) in zip(phases, phases[1:], strict=False) if p < p0]
    if len(wraps) >= 2:
        cycle = statistics.median(b - a for a, b in zip(wraps, wraps[1:], strict=False))

    def coherence(field):
        """Amplitude of the attitude component at the gait frequency / total std."""
        if not cycle or cycle <= 0:
            return None, None
        vals = [(float(r["elapsed_ms"]) / 1000, num(r[field]) or 0.0) for r in ordered]
        mean = statistics.fmean(v for _, v in vals)
        sin_sum = sum((v - mean) * math.sin(2 * math.pi * t / cycle) for t, v in vals)
        cos_sum = sum((v - mean) * math.cos(2 * math.pi * t / cycle) for t, v in vals)
        amp = 2 * math.hypot(sin_sum, cos_sum) / len(vals)
        spread = statistics.pstdev([v for _, v in vals]) or 1e-9
        return amp, amp / spread

    roll_amp, roll_coh = coherence("control_roll")
    pitch_amp, pitch_coh = coherence("control_pitch")

    contact = {}
    for leg in ("fr", "fl", "rr", "rl"):
        vals = [num(r.get(f"contact_{leg}")) for r in ordered]
        vals = [v for v in vals if v is not None]
        contact[leg] = (sum(vals) / len(vals) * 100) if vals else None

    return {
        "run": run, "motion": motion, "mode": mode, "dur": dur,
        "travel_s": travel / dur,
        "pinned": pinned / max(1, axes_used) * 100,
        "cycle": cycle,
        "roll_amp": roll_amp, "roll_coh": roll_coh,
        "pitch_amp": pitch_amp, "pitch_coh": pitch_coh,
        "contact": contact,
    }


results = []
for run in RUNS:
    for motion, mode, start, end in segments(run):
        r = analyse(run, motion, mode, start, end)
        if r:
            results.append(r)

print(f"{'motion':<20}{'mode':<9}{'dur':>5}{'cycle':>7}{'travel/s':>9}"
      f"{'pinned%':>8}{'pitchAmp':>9}{'pitchCoh':>9}{'rollAmp':>8}{'rollCoh':>8}")
for r in sorted(results, key=lambda x: -(x["pitch_coh"] or 0)):
    def f(v, d=2):
        return "-" if v is None else f"{v:.{d}f}"
    print(f"{r['motion']:<20}{r['mode']:<9}{r['dur']:>5.0f}{f(r['cycle'],1):>7}"
          f"{r['travel_s']:>9.0f}{r['pinned']:>8.1f}"
          f"{f(r['pitch_amp']):>9}{f(r['pitch_coh']):>9}{f(r['roll_amp']):>8}{f(r['roll_coh']):>8}")

any_contact = any(v is not None and v > 0 for r in results for v in r["contact"].values())
if any_contact:
    print(f"\n{'motion':<20} contact duty %% (FR/FL/RR/RL)")
    for r in results:
        c = r["contact"]
        print(f"{r['motion']:<20} " + " ".join(
            "-" if c[k] is None else f"{c[k]:5.1f}" for k in ("fr", "fl", "rr", "rl")))
else:
    print("\n(no foot-contact data in these logs)")
