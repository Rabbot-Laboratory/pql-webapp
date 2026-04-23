"""
固定モーションCSVをキュービックイーズイン/アウト補間で滑らかかつダイナミックに変換するスクリプト。

使い方:
    python scripts/smooth_motions.py

各CSVのオリジナルは <name>_original.csv としてバックアップされます。
"""

import csv
from pathlib import Path
from typing import NamedTuple


# ---------------------------------------------------------------------------
# イージング関数
# ---------------------------------------------------------------------------

def ease_in_out_cubic(t: float) -> float:
    """
    Cubic ease-in/out: ゆっくり加速 → 素早く移動 → ゆっくり減速。
    sinusoidal より加減速が急で「ダイナミックな動感」が出る。
    t: 0.0〜1.0
    """
    if t < 0.5:
        return 4.0 * t ** 3
    else:
        return 1.0 - (-2.0 * t + 2.0) ** 3 / 2.0


# ---------------------------------------------------------------------------
# モーションごとの設定
# ---------------------------------------------------------------------------

class MotionConfig(NamedTuple):
    steps_per_keyframe: int   # キーフレーム間を何分割するか
    interval_sec: float       # 1サブフレームあたりの時間(秒)


MOTION_CONFIGS: dict[str, MotionConfig] = {
    # crawl: ゆっくりした歩容 → 少し多めのステップで流れるような動き
    "crawl.csv": MotionConfig(steps_per_keyframe=12, interval_sec=0.022),
    # trot: 対角ペア歩容 → 軽快さを出すためやや少なめのステップ
    "trot.csv":  MotionConfig(steps_per_keyframe=10, interval_sec=0.022),
    # pace: 側方ペア歩容 → trotと同様
    "pace.csv":  MotionConfig(steps_per_keyframe=10, interval_sec=0.022),
    # bound: 全脚同時ジャンプ → 短いステップでパワフルな弾け感
    "bound.csv": MotionConfig(steps_per_keyframe=8,  interval_sec=0.025),
}


# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------

def parse_motion_csv(path: Path) -> tuple[dict[str, str], list[list[int]]]:
    """ヘッダーコメントとデータ行をパースする。"""
    headers: dict[str, str] = {}
    rows: list[list[int]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith("#"):
                key, _, value = line[1:].partition("=")
                headers[key.strip()] = value.strip()
            else:
                rows.append([int(v.strip()) for v in line.split(",")])
    return headers, rows


def interpolate_rows(
    rows: list[list[int]],
    steps: int,
) -> list[list[int]]:
    """
    連続するキーフレーム間を cubic ease-in/out で補間する。
    ループ再生を前提に、最終フレームから最初のフレームへの補間も生成する。
    """
    result: list[list[int]] = []
    n = len(rows)
    for i in range(n):
        current = rows[i]
        next_row = rows[(i + 1) % n]   # 末尾は先頭へ折り返し（ループ補間）
        for step in range(steps):
            t = step / steps
            e = ease_in_out_cubic(t)
            interp = [
                round(current[j] + (next_row[j] - current[j]) * e)
                for j in range(len(current))
            ]
            result.append(interp)
    return result


def write_motion_csv(
    path: Path,
    rows: list[list[int]],
    interval_sec: float,
    loop: bool,
) -> None:
    """補間済みデータを control_service.py が読めるフォーマットで書き出す。"""
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(f"# interval_sec={interval_sec}\n")
        f.write(f"# loop={'true' if loop else 'false'}\n")
        f.write("# advance_mode=time\n")
        # time モードでは position_tolerance / pressure_threshold は使われないが
        # フォーマット互換性のために記載しておく
        f.write("# position_tolerance=160\n")
        f.write("# pressure_threshold=0\n")
        f.write("# step_timeout_sec=1.5\n")
        f.write("# settle_time_sec=0.1\n")
        writer = csv.writer(f)
        for row in rows:
            writer.writerow(row)


# ---------------------------------------------------------------------------
# メイン処理
# ---------------------------------------------------------------------------

def process_file(path: Path, config: MotionConfig) -> None:
    headers, rows = parse_motion_csv(path)
    if not rows:
        print(f"  SKIP {path.name}: データ行なし")
        return

    # オリジナルをバックアップ（初回のみ）
    backup_path = path.with_stem(path.stem + "_original")
    if not backup_path.exists():
        backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"  バックアップ: {backup_path.name}")

    loop = headers.get("loop", "true").lower() == "true"
    interpolated = interpolate_rows(rows, config.steps_per_keyframe)
    write_motion_csv(path, interpolated, config.interval_sec, loop)

    total_frames = len(interpolated)
    cycle_sec = total_frames * config.interval_sec
    print(
        f"  {path.name}: {len(rows)} keyframes"
        f" → {total_frames} frames"
        f" | {config.interval_sec * 1000:.0f}ms/frame"
        f" | {cycle_sec:.2f}s/cycle"
    )


def main() -> None:
    fixed_motion_dir = Path(__file__).resolve().parents[1] / "motion" / "fixed"
    print(f"対象ディレクトリ: {fixed_motion_dir}\n")

    for name, config in MOTION_CONFIGS.items():
        path = fixed_motion_dir / name
        if not path.exists():
            print(f"[{name}] SKIP: ファイルが見つかりません")
            continue
        print(f"[{name}]")
        process_file(path, config)

    print("\n完了。元に戻す場合は *_original.csv をリネームしてください。")


if __name__ == "__main__":
    main()
