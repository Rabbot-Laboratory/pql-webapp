# 2026-08-23 実測パラメータ駆動の歩行候補(MuJoCo)

空圧モデルは実測値(bang probe)ベース: `config/pneumatic_sim.measured.json`。
旧仮パラメータ比で速度1/3〜1/10・遅延2〜5倍。この条件で現行 rabbit_bound(2.2s)は
25sで+0.036 m(実機の「動くが進まない」を再現)。

実現性 = 指令波形の最大スロープ ÷ 実測軸速度(1.0超は物理的に追従不能、採用基準 0.7 以下)。

| pattern | cycle_s | stride_m | feas(worst axis) | forward/cycle | pitch mean | fallen | score |
|---|---|---|---|---|---|---|---|
| trot20 | 20.0 | 0.1 | 1.01 (ax4) | +9.8 cm | 1.19° | no | 0.0956 |
| trot16 | 16.0 | 0.08 | 1.01 (ax4) | +6.1 cm | 0.91° | no | 0.0589 |
| trot_safe | 16.0 | 0.06 | 0.77 (ax4) | +1.6 cm | 0.76° | no | 0.0149 |
| crawl | 20.0 | 0.08 | 1.25 (ax4) | +1.2 cm | 0.73° | no | 0.0105 |
| bound_lowamp | 16.0 | 0.06 | 0.86 (ax4) | +0.5 cm | 0.28° | no | 0.0038 |

## 書き出した候補(パターン別ベスト)

- `walk_trot20.csv` — cycle 20.0s, stride 0.1m, duty 0.6, +9.8 cm/cycle, feas 1.01
- `walk_trot16.csv` — cycle 16.0s, stride 0.08m, duty 0.6, +6.1 cm/cycle, feas 1.01
- `walk_trot_safe.csv` — cycle 16.0s, stride 0.06m, duty 0.6, +1.6 cm/cycle, feas 0.77
- `walk_crawl.csv` — cycle 20.0s, stride 0.08m, duty 0.75, +1.2 cm/cycle, feas 1.25
- `walk_bound_lowamp.csv` — cycle 16.0s, stride 0.06m, duty 0.65, +0.5 cm/cycle, feas 0.86

各CSVは軸別に実測onset遅れぶん位相先行済み(`phase_advance_frames` ヘッダ)。

## 実機での試し方

1. `.env` に `HIGHEND_ADAPTIVE_WALK_MOTION_NAME=walk_crawl` などを設定しサービス再起動
2. 歩行カードで素再生1周期 → walk_metrics → 適応3周期(手順は walking_fix_session_checklist.md)
3. 振幅はGUIの `adaptive_walk_motion_scale`(既定1.0)を50%から上げる
4. 実行前にコンプレッサー稼働・タンク圧を必ず確認(2026-08-23診断の教訓)

再生成: `python scripts/gait_lab.py`
