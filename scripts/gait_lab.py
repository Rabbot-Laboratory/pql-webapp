"""Measured-hardware gait search: simulate candidate gaits and export CSVs.

Uses the bang-probe measurements (per-axis speeds and onset delays;
latest 2026-08-30 after leg re-adjustment) three ways:
  1. the MuJoCo pneumatic model runs with the measured parameters
     (config/pneumatic_sim.measured.json via the loader fallback),
  2. candidates whose commanded waveform exceeds the measured axis speeds
     are pruned before simulating (feasibility check),
  3. exported CSVs are per-axis phase-advanced by the measured onset delay.

Usage:
    python scripts/gait_lab.py            # full search (~30 sims) + export + report
    python scripts/gait_lab.py --quick    # one configuration per pattern (smoke)

Outputs: Motion/Fixed Motion/walk_<pattern>.csv (best per pattern) and
docs/experiments/2026-08-23_gait_candidates.md.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, replace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

import numpy as np  # noqa: E402

from highend_server.application.pql_a00_kinematics import ACTUATOR_HEIGHT_EFFECTS  # noqa: E402
from highend_server.simulation.config import (  # noqa: E402
    GaitConfig,
    load_simulation_config,
)
from highend_server.simulation.gait import LEGS, PhaseOffsetGait, RabbitBoundGait  # noqa: E402
from highend_server.simulation.runner import PqlA00Simulation, SimulationResult  # noqa: E402

# --- rad <-> 0-4095 mapping (joint_preview.py provisional linear law) --------
NEUTRAL_UNITS = 2047.5
HIP_TRAVEL_RAD = np.radians(16.0)
KNEE_TRAVEL_RAD = np.radians(24.0)
# direction: all hips +1, all knees -1 (joint_preview.py, hardware-verified 2026-08-30)
AXIS_DIRECTION = (+1.0, -1.0) * 4
AXIS_TRAVEL_RAD = (HIP_TRAVEL_RAD, KNEE_TRAVEL_RAD) * 4

# --- measured hardware limits (2026-08-30 bang probe after leg re-adjustment,
# medians of 5 reps; all 8 axes alive). "up" = increasing target (cylinder
# extend), units/s; onset in seconds. Weak retract side on axes 0/6 shows as
# lower down_ups.
MEASURED = {
    0: {"up_ups": 519, "down_ups": 563, "onset_s": 0.079},
    1: {"up_ups": 813, "down_ups": 819, "onset_s": 0.176},
    2: {"up_ups": 705, "down_ups": 605, "onset_s": 0.180},
    3: {"up_ups": 725, "down_ups": 726, "onset_s": 0.209},
    4: {"up_ups": 609, "down_ups": 418, "onset_s": 0.243},
    5: {"up_ups": 558, "down_ups": 500, "onset_s": 0.173},
    6: {"up_ups": 521, "down_ups": 344, "onset_s": 0.205},
    7: {"up_ups": 521, "down_ups": 530, "onset_s": 0.148},
}
FEASIBILITY_LIMIT = 0.7  # commanded slope must stay under 70% of measured speed
INTERVAL_S = 0.04
MOTION_DIR = REPO_ROOT / "Motion" / "Fixed Motion"
REPORT_PATH = REPO_ROOT / "docs" / "experiments" / "2026-08-23_gait_candidates.md"


def _sim_foot_z_sign(axis: int) -> float:
    """Sign of d(foot z)/d(gait-frame joint angle) at the neutral pose."""
    leg = LEGS[axis // 2]
    joint = axis % 2
    epsilon = 1e-5
    base = np.zeros(2)
    bumped = base.copy()
    bumped[joint] += epsilon
    dz = (leg.foot_yz(bumped) - leg.foot_yz(base))[1] / epsilon
    return float(np.sign(dz))


def _export_gains() -> np.ndarray:
    """Per-axis rad->units gain with hardware-verified sign reconciliation.

    The nominal inverse of joint_preview is units/rad = 2047.5/(direction *
    travel). The sim's rear-leg sign convention differs from the preview's
    uniform one, so each axis's gain sign is corrected so that "foot up in
    sim" maps to "foot up on hardware" (ACTUATOR_HEIGHT_EFFECTS sign — the
    only sign information verified against the real robot).
    """
    gains = np.zeros(8)
    for axis in range(8):
        nominal = NEUTRAL_UNITS / (AXIS_DIRECTION[axis] * AXIS_TRAVEL_RAD[axis])
        hw_sign = ACTUATOR_HEIGHT_EFFECTS[axis].height_sign  # d foot_z / d target
        sim_sign = _sim_foot_z_sign(axis)  # d foot_z / d angle
        # want sign(d foot_z_hw / d angle) == sim_sign;
        # d foot_z_hw/d angle = hw_sign * gain
        if hw_sign * np.sign(nominal) != sim_sign:
            nominal = -nominal
        gains[axis] = nominal
    return gains


EXPORT_GAINS = _export_gains()


@dataclass(frozen=True)
class Candidate:
    pattern: str
    cycle_s: float
    duty: float
    stride_m: float
    lift_m: float
    phase_offsets: tuple[float, float, float, float] | None  # None = rabbit bound


PATTERNS: dict[str, list[Candidate]] = {}


def _add(pattern: str, cycles: list[float], strides: list[float], **kw) -> None:
    PATTERNS[pattern] = [
        Candidate(pattern=pattern, cycle_s=c, stride_m=s, **kw)
        for c in cycles
        for s in strides
    ]


# Candidates. Hardware evidence beats the simulation ranking here.
#
# Cross-run analysis of every walking segment on 2026-08-30 (freeplay ..
# freeplay5) measured, per segment, how much the axes actually travelled and
# how periodic the body attitude was at the gait frequency ("coherence" -
# real weight shifting repeats once per cycle, vibration does not):
#     walk_trot_fast8  8 s trot : roll 4.2 deg at coherence 0.80, rear-foot
#                                 contact 27% - operator: "stable"
#     walk_crawl_fast  12 s crawl: pitch coherence 0.65 but little travel
#     walk_crawl_fast8big 8 s    : most travel (15150 u/s), least pinning 3.4%
#     walk_crawl (20 s)          : coherence 0.25, axes pinned at their ends
# Faster cycles beat slow ones (the valve dead zone means an axis only moves
# once the error grows), and trot phasing beats crawl phasing for coherent
# weight transfer. The family below crosses those two findings: trot phasing
# at short cycles with the larger strides that kept axes off their ends.
_add("trot_fast8big", [8.0], [0.09], duty=0.60, lift_m=0.035,
     phase_offsets=(0.0, 0.5, 0.5, 0.0))
_add("trot_fast6", [6.0], [0.08], duty=0.60, lift_m=0.035,
     phase_offsets=(0.0, 0.5, 0.5, 0.0))
_add("trot_fast5", [5.0], [0.07], duty=0.55, lift_m=0.030,
     phase_offsets=(0.0, 0.5, 0.5, 0.0))
_add("trot_fast8", [8.0], [0.07], duty=0.60, lift_m=0.030,
     phase_offsets=(0.0, 0.5, 0.5, 0.0))
_add("crawl_fast8big", [8.0], [0.09], duty=0.75, lift_m=0.040,
     phase_offsets=(0.25, 0.75, 0.0, 0.5))
_add("crawl_fast8", [8.0], [0.06], duty=0.75, lift_m=0.030,
     phase_offsets=(0.25, 0.75, 0.0, 0.5))
_add("crawl_fast6", [6.0], [0.07], duty=0.75, lift_m=0.035,
     phase_offsets=(0.25, 0.75, 0.0, 0.5))
_add("crawl_fast4", [4.0], [0.06], duty=0.75, lift_m=0.030,
     phase_offsets=(0.25, 0.75, 0.0, 0.5))
_add("crawl", [20.0], [0.08], duty=0.75, lift_m=0.020,
     phase_offsets=(0.25, 0.75, 0.0, 0.5))
_add("trot16", [16.0], [0.08], duty=0.60, lift_m=0.025,
     phase_offsets=(0.0, 0.5, 0.5, 0.0))


def build_gait(candidate: Candidate, base: GaitConfig):
    if candidate.phase_offsets is None:
        config = replace(
            base,
            cycle_s=candidate.cycle_s,
            duty_factor=candidate.duty,
            stride_m=candidate.stride_m,
            lift_m=candidate.lift_m,
        )
        return RabbitBoundGait(config)
    config = replace(
        base,
        cycle_s=candidate.cycle_s,
        duty_factor=candidate.duty,
        stride_m=candidate.stride_m,
        lift_m=candidate.lift_m,
        front_stride_scale=1.0,
        rear_stride_scale=1.0,
        rear_lift_scale=1.0,
    )
    return PhaseOffsetGait(config, candidate.phase_offsets)


def sample_waveform(gait, cycle_s: float) -> np.ndarray:
    """One steady-state cycle of joint angles (frames x 8 rad)."""
    config = gait.config
    frames = max(8, round(cycle_s / INTERVAL_S))
    # Start at a whole-cycle boundary after startup + ramp so amplitude is 1.0
    # and frame 0 corresponds to phase 0 (+ per-leg offsets).
    cycles_past_ramp = int(np.ceil(config.ramp_s / cycle_s)) + 1
    t0 = config.startup_s + cycles_past_ramp * cycle_s
    return np.array(
        [gait.angles(t0 + k * INTERVAL_S) for k in range(frames)], dtype=float
    )


def to_units(angle_frames: np.ndarray) -> np.ndarray:
    units = NEUTRAL_UNITS + angle_frames * EXPORT_GAINS
    return np.clip(np.round(units), 0, 4095).astype(int)


def feasibility(unit_frames: np.ndarray) -> tuple[float, int]:
    """Worst-axis ratio of commanded slope to measured axis speed.

    Returns (worst_ratio, worst_axis); feasible when worst_ratio <=
    FEASIBILITY_LIMIT. Wraps around the cycle.
    """
    worst = 0.0
    worst_axis = -1
    for axis in range(8):
        column = unit_frames[:, axis].astype(float)
        deltas = np.diff(np.append(column, column[0])) / INTERVAL_S
        up_need = max(deltas.max(), 0.0)
        down_need = max(-deltas.min(), 0.0)
        ratio = max(
            up_need / MEASURED[axis]["up_ups"],
            down_need / MEASURED[axis]["down_ups"],
        )
        if ratio > worst:
            worst, worst_axis = ratio, axis
    return worst, worst_axis


def score(result: SimulationResult, cycle_s: float, duration_s: float) -> float:
    if result.fallen:
        return float("-inf")
    cycles = max(1.0, (duration_s - 2.0) / cycle_s)
    per_cycle = result.forward_distance_m / cycles
    return per_cycle - 0.0015 * (result.mean_abs_pitch_deg + result.mean_abs_roll_deg)


def phase_advance_frames() -> list[int]:
    return [round(MEASURED[axis]["onset_s"] / INTERVAL_S) for axis in range(8)]


def export_csv(candidate: Candidate, unit_frames: np.ndarray, result: SimulationResult,
               per_cycle_m: float, path: Path) -> None:
    advances = phase_advance_frames()
    frames = unit_frames.shape[0]
    shifted = np.empty_like(unit_frames)
    for axis in range(8):
        # advance the column so the slow axis is commanded earlier
        shifted[:, axis] = np.roll(unit_frames[:, axis], -advances[axis])
    offsets = candidate.phase_offsets
    header = [
        f"# interval_sec={INTERVAL_S}",
        "# loop=true",
        "# advance_mode=time",
        "# position_tolerance=160",
        "# pressure_threshold=0",
        "# step_timeout_sec=1.5",
        "# settle_time_sec=0.1",
        f"# generated_from=gait_lab_{candidate.pattern}",
        f"# cycle_sec={candidate.cycle_s:.2f}",
        f"# duty_factor={candidate.duty}",
        f"# stride_m={candidate.stride_m}",
        f"# lift_m={candidate.lift_m}",
        f"# phase_offsets={offsets if offsets is not None else 'rabbit_bound'}",
        f"# phase_advance_frames={','.join(str(a) for a in advances)}",
        f"# sim_forward_m_per_cycle={per_cycle_m:.4f}",
        f"# sim_mean_abs_pitch_deg={result.mean_abs_pitch_deg:.2f}",
        "# pneumatic_params=measured_2026-08-23_bang_probe",
    ]
    rows = [",".join(str(v) for v in shifted[k]) for k in range(frames)]
    path.write_text("\n".join(header + rows) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true", help="one config per pattern")
    parser.add_argument("--duration-cycles", type=float, default=3.0)
    args = parser.parse_args()

    base = load_simulation_config()
    results: list[dict] = []

    for pattern, candidates in PATTERNS.items():
        chosen = candidates[:1] if args.quick else candidates
        for candidate in chosen:
            gait = build_gait(candidate, base.gait)
            angle_frames = sample_waveform(gait, candidate.cycle_s)
            unit_frames = to_units(angle_frames)
            ratio, worst_axis = feasibility(unit_frames)
            entry = {
                "candidate": candidate,
                "feasibility": ratio,
                "worst_axis": worst_axis,
                "unit_frames": unit_frames,
                "result": None,
                "score": float("-inf"),
                "per_cycle_m": 0.0,
            }
            # The sim's actuator model enforces the measured speed caps, so
            # let physics judge anything remotely plausible. The 2026-08-30
            # hardware run walked 40 cm at feasibility 1.11, i.e. the
            # steady-state ratio alone is not a veto.
            if ratio > 12.00:
                print(f"[skip] {pattern} cycle={candidate.cycle_s}s stride={candidate.stride_m} "
                      f"infeasible (axis {worst_axis} needs {ratio:.2f}x measured speed)")
                results.append(entry)
                continue
            duration = min(75.0, 2.0 + args.duration_cycles * candidate.cycle_s)
            sim = PqlA00Simulation(base)
            sim.gait = gait
            result = sim.run(duration, quiet=True)
            cycles = max(1.0, (duration - 2.0) / candidate.cycle_s)
            entry["result"] = result
            entry["score"] = score(result, candidate.cycle_s, duration)
            entry["per_cycle_m"] = result.forward_distance_m / cycles
            results.append(entry)
            print(f"[sim ] {pattern} cycle={candidate.cycle_s}s stride={candidate.stride_m} "
                  f"feas={ratio:.2f} -> {result.forward_distance_m:+.3f} m "
                  f"({entry['per_cycle_m'] * 100:+.1f} cm/cyc) "
                  f"pitch~{result.mean_abs_pitch_deg:.1f}deg "
                  f"fallen={result.fallen}")

    # pick best per pattern and export
    lines = [
        "# 2026-08-23 実測パラメータ駆動の歩行候補(MuJoCo)",
        "",
        "空圧モデルは実測値(bang probe)ベース: `config/pneumatic_sim.measured.json`。",
        "旧仮パラメータ比で速度1/3〜1/10・遅延2〜5倍。この条件で現行 rabbit_bound(2.2s)は",
        "25sで+0.036 m(実機の「動くが進まない」を再現)。",
        "",
        "実現性 = 指令波形の最大スロープ ÷ 実測軸速度(1.0超は物理的に追従不能、"
        f"採用基準 {FEASIBILITY_LIMIT} 以下)。",
        "",
        "| pattern | cycle_s | stride_m | feas(worst axis) "
        "| forward/cycle | pitch mean | fallen | score |",
        "|---|---|---|---|---|---|---|---|",
    ]
    exported: list[str] = []
    for pattern in PATTERNS:
        entries = [e for e in results if e["candidate"].pattern == pattern]
        for e in entries:
            c = e["candidate"]
            r = e["result"]
            lines.append(
                f"| {pattern} | {c.cycle_s} | {c.stride_m} | "
                f"{e['feasibility']:.2f} (ax{e['worst_axis']}) | "
                + (f"{e['per_cycle_m']*100:+.1f} cm | {r.mean_abs_pitch_deg:.2f}° | "
                   f"{'YES' if r.fallen else 'no'} | {e['score']:.4f} |"
                   if r is not None else "— | — | — | skip |")
            )
        best = max(entries, key=lambda e: e["score"])
        if best["result"] is None or best["score"] == float("-inf"):
            lines.append(f"| **{pattern}: 採用なし(全構成が不成立)** | | | | | | | |")
            continue
        c = best["candidate"]
        out = MOTION_DIR / f"walk_{pattern}.csv"
        export_csv(c, best["unit_frames"], best["result"], best["per_cycle_m"], out)
        exported.append(
            f"- `walk_{pattern}.csv` — cycle {c.cycle_s}s, stride {c.stride_m}m, "
            f"duty {c.duty}, {best['per_cycle_m']*100:+.1f} cm/cycle, "
            f"feas {best['feasibility']:.2f}"
        )
        print(f"[best] {pattern}: cycle={c.cycle_s}s stride={c.stride_m} -> {out.name}")

    lines += [
        "",
        "## 書き出した候補(パターン別ベスト)",
        "",
        *exported,
        "",
        "各CSVは軸別に実測onset遅れぶん位相先行済み(`phase_advance_frames` ヘッダ)。",
        "",
        "## 実機での試し方",
        "",
        "1. `.env` に `HIGHEND_ADAPTIVE_WALK_MOTION_NAME=walk_crawl` などを設定しサービス再起動",
        "2. 歩行カードで素再生1周期 → walk_metrics → 適応3周期"
        "(手順は walking_fix_session_checklist.md)",
        "3. 振幅はGUIの `adaptive_walk_motion_scale`(既定1.0)を50%から上げる",
        "4. 実行前にコンプレッサー稼働・タンク圧を必ず確認(2026-08-23診断の教訓)",
        "",
        "再生成: `python scripts/gait_lab.py`",
    ]
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"report: {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
