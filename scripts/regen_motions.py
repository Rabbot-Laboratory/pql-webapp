"""
Regenerate mathematically correct gait CSV files for the quadruped robot.

Gaits generated:
  trot  - diagonal pairs (FR+RL vs FL+RR), 180° phase offset
  pace  - lateral/ipsilateral pairs (FR+RR vs FL+RL), 180° phase offset
  bound - fore/hind pairs (FR+FL vs RR+RL), 180° phase offset
  crawl - 4-beat wave: FR=90°, FL=270°, RR=0°, RL=180°

Actuator order in CSV row: FR-hip, FR-knee, FL-hip, FL-knee, RR-hip, RR-knee, RL-hip, RL-knee
"""

import math
import csv
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

OUTPUT_DIR = Path(__file__).resolve().parents[1] / "motion" / "fixed"

# ---------------------------------------------------------------------------
# Easing
# ---------------------------------------------------------------------------

def ease_in_out_cubic(t: float) -> float:
    """Cubic ease-in/out: smooth acceleration and deceleration."""
    if t < 0.5:
        return 4.0 * t ** 3
    else:
        return 1.0 - (-2.0 * t + 2.0) ** 3 / 2.0

# ---------------------------------------------------------------------------
# Sinusoidal leg position
# ---------------------------------------------------------------------------

def leg_pos(phase_deg: float) -> tuple[int, int]:
    """
    Given a phase in degrees, return (hip, knee) servo positions.

    hip  = 2048 + 1365 * sin(phase_rad)   → HIGH(>2048)=backward/stance, LOW(<2048)=forward/swing
    knee = 2048 - 1365 * sin(phase_rad)   → inverse of hip
    Both clamped to [0, 4095].
    """
    phase_rad = math.radians(phase_deg)
    hip  = round(2048 + 1365 * math.sin(phase_rad))
    knee = round(2048 - 1365 * math.sin(phase_rad))
    hip  = max(0, min(4095, hip))
    knee = max(0, min(4095, knee))
    return hip, knee

# ---------------------------------------------------------------------------
# Keyframe generation
# ---------------------------------------------------------------------------

def generate_keyframes(offsets: tuple[float, float, float, float], n_frames: int) -> list[list[int]]:
    """
    Generate n_frames keyframes for the given leg phase offsets.

    offsets: (FR_offset, FL_offset, RR_offset, RL_offset) in degrees.
    Returns list of rows, each row = [FR-hip, FR-knee, FL-hip, FL-knee,
                                       RR-hip, RR-knee, RL-hip, RL-knee]
    """
    fr_off, fl_off, rr_off, rl_off = offsets
    keyframes = []
    for i in range(n_frames):
        base_phase = i * (360.0 / n_frames)
        fr_hip, fr_knee = leg_pos(base_phase + fr_off)
        fl_hip, fl_knee = leg_pos(base_phase + fl_off)
        rr_hip, rr_knee = leg_pos(base_phase + rr_off)
        rl_hip, rl_knee = leg_pos(base_phase + rl_off)
        keyframes.append([fr_hip, fr_knee, fl_hip, fl_knee, rr_hip, rr_knee, rl_hip, rl_knee])
    return keyframes

# ---------------------------------------------------------------------------
# Interpolation between keyframes
# ---------------------------------------------------------------------------

def interpolate_keyframes(keyframes: list[list[int]], steps_per_keyframe: int) -> list[list[int]]:
    """
    Interpolate between consecutive keyframes (with wrap-around) using cubic ease-in/out.

    For each pair (kf[i], kf[i+1 % n]), generate steps_per_keyframe intermediate frames.
    The first step is always the start keyframe (t=0); the last keyframe is NOT included
    to avoid duplication (it will be the start of the next segment).
    """
    n = len(keyframes)
    frames = []
    for i in range(n):
        start = keyframes[i]
        end   = keyframes[(i + 1) % n]
        for s in range(steps_per_keyframe):
            t = s / steps_per_keyframe          # t in [0, 1)
            e = ease_in_out_cubic(t)
            row = [round(start[j] + e * (end[j] - start[j])) for j in range(8)]
            frames.append(row)
    return frames

# ---------------------------------------------------------------------------
# CSV writing
# ---------------------------------------------------------------------------

HEADERS = [
    "# interval_sec={interval}",
    "# loop=true",
    "# advance_mode=time",
    "# position_tolerance=160",
    "# pressure_threshold=0",
    "# step_timeout_sec=1.5",
    "# settle_time_sec=0.1",
]

def write_csv(path: Path, frames: list[list[int]], interval_sec: float) -> None:
    """Write gait frames to a CSV file with the required headers."""
    with open(path, "w", newline="") as f:
        # Write comment headers
        for h in HEADERS:
            f.write(h.format(interval=interval_sec) + "\n")
        # Write data rows
        writer = csv.writer(f)
        for row in frames:
            writer.writerow(row)

# ---------------------------------------------------------------------------
# Gait definitions
# ---------------------------------------------------------------------------

GAITS = {
    "trot": {
        "offsets":            (90, 270, 270, 90),   # FR+RL same phase; FL+RR 180° offset
        "n_frames":           8,
        "steps_per_keyframe": 10,
        "interval_sec":       0.022,
    },
    "pace": {
        "offsets":            (90, 270, 90, 270),   # FR+RR same phase; FL+RL 180° offset
        "n_frames":           8,
        "steps_per_keyframe": 10,
        "interval_sec":       0.022,
    },
    "bound": {
        "offsets":            (90, 90, 270, 270),   # FR+FL same phase; RR+RL 180° offset
        "n_frames":           8,
        "steps_per_keyframe": 8,
        "interval_sec":       0.025,
    },
    "crawl": {
        "offsets":            (90, 270, 0, 180),    # 4-beat wave: FR=90, FL=270, RR=0, RL=180
        "n_frames":           16,
        "steps_per_keyframe": 8,
        "interval_sec":       0.030,
    },
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for gait_name, cfg in GAITS.items():
        offsets            = cfg["offsets"]
        n_frames           = cfg["n_frames"]
        steps_per_keyframe = cfg["steps_per_keyframe"]
        interval_sec       = cfg["interval_sec"]

        keyframes    = generate_keyframes(offsets, n_frames)
        frames       = interpolate_keyframes(keyframes, steps_per_keyframe)
        total_frames = len(frames)
        cycle_ms     = total_frames * interval_sec * 1000

        out_path = OUTPUT_DIR / f"{gait_name}.csv"
        write_csv(out_path, frames, interval_sec)

        print(
            f"{gait_name:6s}: {n_frames} keyframes × {steps_per_keyframe} steps "
            f"= {total_frames} total frames | "
            f"{interval_sec*1000:.1f} ms/frame | "
            f"cycle = {cycle_ms:.0f} ms"
        )

    print("\nAll gaits written to:", OUTPUT_DIR)


if __name__ == "__main__":
    main()
