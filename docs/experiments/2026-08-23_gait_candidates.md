# 2026-08-23 実測パラメータ駆動の歩行候補(MuJoCo)

空圧モデルは実測値(bang probe)ベース: `config/pneumatic_sim.measured.json`。
旧仮パラメータ比で速度1/3〜1/10・遅延2〜5倍。この条件で現行 rabbit_bound(2.2s)は
25sで+0.036 m(実機の「動くが進まない」を再現)。

実現性 = 指令波形の最大スロープ ÷ 実測軸速度(1.0超は物理的に追従不能、採用基準 0.7 以下)。

| pattern | cycle_s | stride_m | feas(worst axis) | forward/cycle | pitch mean | fallen | score |
|---|---|---|---|---|---|---|---|
| trot20 | 20.0 | 0.1 | 1.00 (ax4) | +9.8 cm | 1.19° | no | 0.0956 |
| trot16 | 16.0 | 0.08 | 1.00 (ax4) | +6.1 cm | 0.91° | no | 0.0589 |
| trot_safe | 16.0 | 0.06 | 0.76 (ax4) | +1.6 cm | 0.76° | no | 0.0149 |
| crawl | 20.0 | 0.08 | 1.24 (ax4) | +1.2 cm | 0.73° | no | 0.0105 |
| bound_lowamp | 16.0 | 0.06 | 0.86 (ax4) | +0.5 cm | 0.28° | no | 0.0038 |

## 書き出した候補(パターン別ベスト)

- `walk_trot20.csv` — cycle 20.0s, stride 0.1m, duty 0.6, +9.8 cm/cycle, feas 1.00
- `walk_trot16.csv` — cycle 16.0s, stride 0.08m, duty 0.6, +6.1 cm/cycle, feas 1.00
- `walk_trot_safe.csv` — cycle 16.0s, stride 0.06m, duty 0.6, +1.6 cm/cycle, feas 0.76
- `walk_crawl.csv` — cycle 20.0s, stride 0.08m, duty 0.75, +1.2 cm/cycle, feas 1.24
- `walk_bound_lowamp.csv` — cycle 16.0s, stride 0.06m, duty 0.65, +0.5 cm/cycle, feas 0.86

各CSVは軸別に実測onset遅れぶん位相先行済み(`phase_advance_frames` ヘッダ)。

## 実機での試し方

1. `.env` に `HIGHEND_ADAPTIVE_WALK_MOTION_NAME=walk_crawl` などを設定しサービス再起動
2. 歩行カードで素再生1周期 → walk_metrics → 適応3周期(手順は walking_fix_session_checklist.md)
3. 振幅はGUIの `adaptive_walk_motion_scale`(既定1.0)を50%から上げる
4. 実行前にコンプレッサー稼働・タンク圧を必ず確認(2026-08-23診断の教訓)

## 探索の経緯(なぜこの5つか)

1. **当初グリッド(周期3〜8s)は全滅**: 実測軸速度(最遅=右後膝456u/s)に対し
   指令スロープが5〜15倍超過。8/11に実機で歩けなかった直接の再現。
2. **周期12〜20s×小ストライド(3〜4cm)は追従可能だが前進ほぼゼロ**:
   旧・高速パラメータでも同じくゼロ → 重心移動のない準静歩行は物理的に推進しない
   (滑りが対称に相殺)。liftを増やすと悪化(-0.8cm/周期)。
3. **切り分け実験**: 高速パラメータではトロット8s/4cmが+3.6cm/周期
   → トロットの推進機構は健在で、追従性だけが障壁と確定。
4. **解=長周期×大ストライド**: 周期を16〜20sまで伸ばすと大ストライド(6〜10cm)でも
   スロープが実測速度内に収まり、前進が急回復(トロット20s/10cm: **+9.8cm/周期**)。
5. **棄却**: pronk(全脚同時)は周期12sでも膝速度1.15〜2.25倍超過で不成立。
   rabbit系はキック圧縮の性質上、緩和(kick_fraction 0.7)しても1.66倍超過で不成立。
   pace(同側)は後退した(-1.2cm/周期)。いずれも軸速度が改善したら再評価。

## 5候補の位置づけ

| CSV | 性格 | 実機での使いどころ |
|---|---|---|
| walk_trot_safe | 安全マージン重視(実現性0.76) | **最初に試すのはこれ** |
| walk_trot16 | 主力(+6.1cm/周期) | trot_safeが動いたら |
| walk_trot20 | 最速(+9.8cm/周期、マージンなし) | ゲイン改善後 |
| walk_crawl | 常時3脚接地・転倒リスク最小(実現性1.24は要注意) | 姿勢が不安な時 |
| walk_bound_lowamp | バウンド系統の保険(+0.5cm/周期) | 8/11路線の継続検証用 |

## 前提と限界

- 空圧モデルは2026-08-23のbang probe実測(エア良好時)。**ゲイン再調整や
  エア改善で軸速度が上がれば、より速い歩容が解禁される**(gait_lab.pyのMEASUREDを
  更新して再実行)。
- rad→0-4095は暫定線形マッピング+ACTUATOR_HEIGHT_EFFECTSによる符号照合。
  絶対振幅は未校正なので、実機ではGUI振幅(adaptive_walk_motion_scale)を
  50%から慎重に上げること。
- 軸0(右前股)は左前股の左右対称仮定。修理後にbang_probeで実測して差し替え。

再生成: `python scripts/gait_lab.py`(表とファイルのみ再生成。上記の経緯セクションは手動維持)
