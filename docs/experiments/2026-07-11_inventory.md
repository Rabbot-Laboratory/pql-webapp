# 2026-07-11 実機実験ログ・インベントリ

> **ARCHIVED (2026-08-23)** — 当時の計測記録として保存。ここに書かれた推奨事項(Roll符号修正・raw/control列分離・accel_confidence記録 等)は実装済み。現状の正は `docs/codex_walking_loop_prompt.md` の「前提知識」を参照。

生成元: `scripts/analyze_experiment_logs.py`。実験の分類は各 `manifest.json` の `experiment_type` / `name` と記録済みイベントのみを使用している。

| experiment_id | type | duration_sec | Git SHA | frames | telemetry rows | period | gaps | IMU same-run | D項 | PID (R/P) | level offset R/P (deg) | files | valid | remarks |
|---|---|---:|---|---:|---:|---:|---:|---:|---|---|---|---|---|---|
| `20260711_060634_imu-static` | `imu-static` | 85.871 | `b106d08` | 2,146 | 17,168 | 40.00 ms | 0 | 1 | `error_difference` | 1.500/0.000/0.300 ; 1.500/0.000/0.300 | 0.000/0.000 | manifest / telemetry / events / notes | yes | integrity checks passed |
| `20260711_061517_manual-roll` | `manual-roll` | 461.004 | `b106d08` | 11,524 | 92,192 | 40.00 ms | 0 | 1 | `error_difference` | 1.500/0.000/0.300 ; 1.500/0.000/0.300 | 0.000/0.000 | manifest / telemetry / events / notes | yes | integrity checks passed |
| `20260711_062505_manual-pitch` | `manual-pitch` | 196.681 | `b106d08` | 4,916 | 39,328 | 40.00 ms | 0 | 1 | `error_difference` | 1.500/0.000/0.300 ; 1.500/0.000/0.300 | 0.000/0.000 | manifest / telemetry / events / notes | yes | integrity checks passed |
| `20260711_063729_imu-static-calibrated` | `imu-static-calibrated` | 31.761 | `b106d08` | 793 | 6,344 | 40.00 ms | 0 | 1 | `error_difference` | 1.500/0.000/0.300 ; 1.500/0.000/0.300 | -1.845/-0.111 | manifest / telemetry / events / notes | yes | integrity checks passed |
| `20260711_064055_imu-disturbance` | `imu-disturbance` | 231.971 | `b106d08` | 5,798 | 46,384 | 40.00 ms | 0 | 1 | `error_difference` | 1.500/0.000/0.300 ; 1.500/0.000/0.300 | -1.866/-0.058 | manifest / telemetry / events / notes | yes | ADC列なし |

## 完全性チェックの定義

- `elapsed_ms` はフレーム（同一時刻の8アクチュエータ行を1フレームに集約）で単調増加すること。
- 各フレームは8行、数値列は有限値、`effective_target = clamp(base_target + round(stabilization_correction), 0, 4095)` を満たすこと。
- `manifest` の行数・PID値とCSVの値、イベント時刻と記録範囲を照合すること。
- サンプルギャップは `max(期待周期, 実測中央値) × 1.5` を超えるフレーム間隔として数える。

## 記録済みoperator notes

### `20260711_060634_imu-static`

- 0.529 s: `機体完全静止`

### `20260711_061517_manual-roll`

- 0.677 s: `level-start`
- 44.035 s: `roll-right-start`
- 213.176 s: `roll-right-repeat-start`
- 252.894 s: `level-after-right-repeat`
- 422.854 s: `roll-left-start`
- 454.894 s: `level-final`

### `20260711_062505_manual-pitch`

- 0.542 s: `level-start`
- 43.359 s: `nose-up-start`
- 109.679 s: `level-after-nose-up`
- 165.652 s: `nose-down-start`
- 190.576 s: `level-final`

### `20260711_063729_imu-static-calibrated`

- 0.579 s: `after-level-and-gyro-zero-still`

### `20260711_064055_imu-disturbance`

- 0.538 s: `level-start`
- 47.952 s: `translate-forward-back`
- 164.055 s: `translate-left-right`
- 197.863 s: `light-shake-small-impact`
- 225.892 s: `level-final`

