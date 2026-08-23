# PQL-A00: 実機IMU試験と3Dモデルに基づく姿勢補正設計

## 根拠

2026-07-11の手動IMU試験では、水平較正後の生Euler値について以下を確認した。

```text
右側が低い  -> raw Roll は負
ノーズアップ -> raw Pitch は正
```

姿勢補正の制御座標系は、物理的に分かる向きとして次を採用する。

```text
control Roll > 0  : 右側が低い
control Pitch > 0 : ノーズアップ
```

よって制御ループだけで、`control_roll = -(raw_roll - level_roll_offset)`、
`control_pitch = raw_pitch - level_pitch_offset`を使う。生Euler値はAPIとログに
残す。これは完全なIMU取付クォータニオンを推定したものではなく、実機で確認した
Roll/Pitch外側ループの符号変換である。

## URDF由来のアクチュエータモデル

URDFの関節対応は以下である。

| ID | leg | joint | neutral foot-height sensitivity (m / target) |
|---:|---|---|---:|
| 0 | front right | `rev_fr2` | +1.659e-6 |
| 1 | front right | `rev_fr3` | -3.528e-5 |
| 2 | front left | `rev_fl2` | +4.379e-6 |
| 3 | front left | `rev_fl3` | -3.682e-5 |
| 4 | rear right | `rev_rr2` | +8.715e-6 |
| 5 | rear right | `rev_rr3` | -3.916e-5 |
| 6 | rear left | `rev_rl2` | -6.444e-6 |
| 7 | rear left | `rev_rl3` | -2.722e-5 |

値は中立姿勢で、URDFの各lower-link collision meshの最低頂点を足先代表点として
固定し、現在の3D previewのtarget-to-joint角変換を微小差分して得た。

```text
target増加 → シリンダー伸長
target増加 → 足先高さ変化
```

の後段は関節ごとに異なる。そのため「右脚だから全関節を正target補正」とはしない。
混合は各感度の符号を反映し、PID出力が負のとき、右低下なら右足を上げ、ノーズアップ
なら後足を上げることを単体テストで保証する。

感度の**大きさ**はまだ混合へ使わない。空圧圧力、接地、荷重、関節可動域、targetと
実ストロークの非線形性が未同定だからである。逆感度で重み付けするとhipへ大きな補正を
要求する可能性があるため、Phase 9/接地センサ検証前には危険である。

## 実装範囲

- `AttitudeControlFrame`: 実機試験由来のRoll/Pitch符号変換
- `pql_a00_kinematics`: URDF由来のfoot-height感度符号と混合行列
- experiment telemetry: raw / level-corrected / control-frame Roll/Pitchを同時記録
- stabilization: デフォルト混合行列のみ更新。起動時無効、既存の補正上限・自動停止は維持

## 非実装範囲

- アクチュエータの自動駆動・歩行開始
- 圧力・接地センサの制御接続
- 幾何感度の大きさに基づくゲイン配分
- オンライン適応・個体差学習

(追記 2026-08-23: ADC/接地センサはサーバー側検出まで実装済みだが、運用では接地センサを
使わない方針となった。関連機能は `adaptive_walk_use_contact` の既定OFFで無効化されている。)
