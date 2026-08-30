# 2026-08-23 実測パラメータ駆動の歩行候補(MuJoCo)

空圧モデルは実測値(bang probe)ベース: `config/pneumatic_sim.measured.json`。
旧仮パラメータ比で速度1/3〜1/10・遅延2〜5倍。この条件で現行 rabbit_bound(2.2s)は
25sで+0.036 m(実機の「動くが進まない」を再現)。

実現性 = 指令波形の最大スロープ ÷ 実測軸速度(1.0超は物理的に追従不能、採用基準 0.7 以下)。

| pattern | cycle_s | stride_m | feas(worst axis) | forward/cycle | pitch mean | fallen | score |
|---|---|---|---|---|---|---|---|
| trot_fast8big | 8.0 | 0.09 | 2.47 (ax6) | +7.7 cm | 1.30° | no | 0.0739 |
| trot_fast6 | 6.0 | 0.08 | 3.27 (ax6) | +5.3 cm | 1.09° | no | 0.0502 |
| trot_fast5 | 5.0 | 0.07 | 2.91 (ax6) | +3.5 cm | 0.94° | no | 0.0329 |
| trot_fast8 | 8.0 | 0.07 | 2.11 (ax6) | +5.7 cm | 1.03° | no | 0.0546 |
| crawl_fast8big | 8.0 | 0.09 | 4.51 (ax6) | +2.1 cm | 1.07° | no | 0.0185 |
| crawl_fast8 | 8.0 | 0.06 | 3.34 (ax6) | -0.1 cm | 0.77° | no | -0.0030 |
| crawl_fast6 | 6.0 | 0.07 | 4.94 (ax6) | +0.1 cm | 0.70° | no | -0.0005 |
| crawl_fast4 | 4.0 | 0.06 | 6.40 (ax6) | -0.1 cm | 0.48° | no | -0.0019 |
| crawl | 20.0 | 0.08 | 1.11 (ax0) | +1.3 cm | 0.72° | no | 0.0114 |
| trot16 | 16.0 | 0.08 | 0.91 (ax6) | +6.4 cm | 0.87° | no | 0.0626 |

## 書き出した候補(パターン別ベスト)

- `walk_trot_fast8big.csv` — cycle 8.0s, stride 0.09m, duty 0.6, +7.7 cm/cycle, feas 2.47
- `walk_trot_fast6.csv` — cycle 6.0s, stride 0.08m, duty 0.6, +5.3 cm/cycle, feas 3.27
- `walk_trot_fast5.csv` — cycle 5.0s, stride 0.07m, duty 0.55, +3.5 cm/cycle, feas 2.91
- `walk_trot_fast8.csv` — cycle 8.0s, stride 0.07m, duty 0.6, +5.7 cm/cycle, feas 2.11
- `walk_crawl_fast8big.csv` — cycle 8.0s, stride 0.09m, duty 0.75, +2.1 cm/cycle, feas 4.51
- `walk_crawl_fast8.csv` — cycle 8.0s, stride 0.06m, duty 0.75, -0.1 cm/cycle, feas 3.34
- `walk_crawl_fast6.csv` — cycle 6.0s, stride 0.07m, duty 0.75, +0.1 cm/cycle, feas 4.94
- `walk_crawl_fast4.csv` — cycle 4.0s, stride 0.06m, duty 0.75, -0.1 cm/cycle, feas 6.40
- `walk_crawl.csv` — cycle 20.0s, stride 0.08m, duty 0.75, +1.3 cm/cycle, feas 1.11
- `walk_trot16.csv` — cycle 16.0s, stride 0.08m, duty 0.6, +6.4 cm/cycle, feas 0.91

各CSVは軸別に実測onset遅れぶん位相先行済み(`phase_advance_frames` ヘッダ)。

## 実機での試し方

1. `.env` に `HIGHEND_ADAPTIVE_WALK_MOTION_NAME=walk_crawl` などを設定しサービス再起動
2. 歩行カードで素再生1周期 → walk_metrics → 適応3周期(手順は walking_fix_session_checklist.md)
3. 振幅はGUIの `adaptive_walk_motion_scale`(既定1.0)を50%から上げる
4. 実行前にコンプレッサー稼働・タンク圧を必ず確認(2026-08-23診断の教訓)

再生成: `python scripts/gait_lab.py`
