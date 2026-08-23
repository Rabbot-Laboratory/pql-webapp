# 2026-08-23 実測パラメータ駆動の歩行候補(MuJoCo)

空圧モデルは実測値(bang probe)ベース: `config/pneumatic_sim.measured.json`。
旧仮パラメータ比で速度1/3〜1/10・遅延2〜5倍。この条件で現行 rabbit_bound(2.2s)は
25sで+0.036 m(実機の「動くが進まない」を再現)。

実現性 = 指令波形の最大スロープ ÷ 実測軸速度(1.0超は物理的に追従不能、採用基準 0.7 以下)。

| pattern | cycle_s | stride_m | feas(worst axis) | forward/cycle | pitch mean | fallen | score |
|---|---|---|---|---|---|---|---|
| crawl | 12.0 | 0.03 | 1.04 (ax5) | -0.2 cm | 0.30° | no | -0.0023 |
| trot_slow | 8.0 | 0.03 | 0.99 (ax5) | -0.1 cm | 0.36° | no | -0.0014 |
| rabbit_v3 | 8.0 | 0.03 | 2.49 (ax4) | — | — | — | skip |
| **rabbit_v3: 採用なし(全構成が不成立)** | | | | | | | |
| pronk | 6.0 | 0.015 | 2.25 (ax5) | — | — | — | skip |
| **pronk: 採用なし(全構成が不成立)** | | | | | | | |
| bound_lowamp | 8.0 | 0.02 | 1.10 (ax5) | -0.0 cm | 0.20° | no | -0.0009 |

## 書き出した候補(パターン別ベスト)

- `walk_crawl.csv` — cycle 12.0s, stride 0.03m, duty 0.75, -0.2 cm/cycle, feas 1.04
- `walk_trot_slow.csv` — cycle 8.0s, stride 0.03m, duty 0.6, -0.1 cm/cycle, feas 0.99
- `walk_bound_lowamp.csv` — cycle 8.0s, stride 0.02m, duty 0.65, -0.0 cm/cycle, feas 1.10

各CSVは軸別に実測onset遅れぶん位相先行済み(`phase_advance_frames` ヘッダ)。

## 実機での試し方

1. `.env` に `HIGHEND_ADAPTIVE_WALK_MOTION_NAME=walk_crawl` などを設定しサービス再起動
2. 歩行カードで素再生1周期 → walk_metrics → 適応3周期(手順は walking_fix_session_checklist.md)
3. 振幅はGUIの `adaptive_walk_motion_scale`(既定1.0)を50%から上げる
4. 実行前にコンプレッサー稼働・タンク圧を必ず確認(2026-08-23診断の教訓)

再生成: `python scripts/gait_lab.py`
