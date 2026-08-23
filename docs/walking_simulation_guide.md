# PQL-A00 歩行シミュレーション

## 構成

シミュレータには MuJoCo 3.x を採用した。接触を含む多関節ロボットを高速に計算でき、Windows 上で GUI とヘッドレス実行の両方が使え、Python から空圧応答や適応則を直接実装できるためである。

- `simulation/pql_a00.xml`: CAD/URDF 由来の寸法、質量、慣性、関節軸、脚 STL を使う物理モデル
- `config/pneumatic_sim.json`: 8本のシリンダごとの遅延、伸縮速度、時定数、デッドバンド、トルク相当値
- `src/highend_server/simulation/gait.py`: 足先軌道、CAD 寸法からの逆運動学、後脚主体のラビットバウンド歩容
- `src/highend_server/simulation/actuator.py`: 空圧応答モデルと軸別の適応位相進み制御
- `src/highend_server/simulation/imu_control.py`: 遅延・ノイズ・バイアス付き仮想IMUと適応姿勢トリム
- `src/highend_server/simulation/runner.py`: MuJoCo との接続、計測、CSV ログ

元の `base_link.stl` は336,714面あり、MuJoCo の STL 1個あたり200,000面という読込上限を超える。そのため胴体表示だけは実寸外形の2個の箱に置き換えた。胴体の質量・重心・慣性はURDF値を使用し、12個の脚部STLはそのまま表示する。物理接触にはCADの細かい凹凸ではなく、カプセルと球の単純形状を使う。これは接触計算の不安定化を避けるためである。

## インストールと実行

```powershell
python -m pip install -e ".[simulation,dev]"
```

GUIで12秒間歩かせる。

```powershell
python scripts\run_simulation.py --duration 12
```

自動評価用のヘッドレス実行とログ保存。

```powershell
python scripts\run_simulation.py --headless --duration 20 --log Logs\simulation\rabbit_bound.csv
```

適応制御との比較。

```powershell
python scripts\run_simulation.py --headless --duration 12 --no-adaptation
```

IMU姿勢制御だけを無効にして比較する。

```powershell
python scripts\run_simulation.py --headless --duration 12 --no-imu-control
```

## IMUを含む適応歩行制御

接地センサは使用しない。MuJoCoの胴体上に `imu_site` を置き、姿勢Quaternion、3軸角速度、3軸加速度を生成する。Python側の仮想IMUは、実機へ移す前に制御の遅延耐性を確認できるよう、サンプリング周期、通信・推定遅延、姿勢ノイズ、角速度ノイズ、バイアスを加える。

制御は二層で動く。

1. 軸別適応：各シリンダの追従誤差から位相進み時間を学習する。
2. IMU適応：Roll/Pitchと角速度から足先高さを前後・左右へ配分し、持続する傾きをRoll/Pitchトリムとしてオンライン学習する。

IMU補正は1脚あたり `max_foot_correction_m`、学習トリムは `max_trim_deg`、瞬時の補正傾斜は `max_slope_deg` で制限する。接地情報がないため、支持脚と遊脚を判定せず4脚すべての基準軌道へ補正を加える。

初期設定での35秒評価は次のとおり。

| 制御 | 前進距離 | 最大X/Y姿勢角 | 平均絶対X/Y姿勢角 | 転倒 |
|---|---:|---:|---:|---:|
| IMU適応あり | 1.177 m | 4.1° / 4.0° | 1.78° / 1.07° | なし |

ここでRoll/PitchはMuJoCo/CAD座標のX軸・Y軸回転であり、実機BMX055の表示座標へ移す際には既存の `AttitudeControlFrame` と同じ取付方向・符号確認が必要になる。

終了時に前進距離、横ずれ、胴体高、最大 Roll/Pitch、平均関節追従誤差、転倒判定、各軸が学習した位相進み時間を表示する。現行のラビットバウンドは、膝を深く折り過ぎない中間姿勢で後脚を同期させ、接地序盤に短く強く蹴り出す。前脚は交互支持で姿勢と着地を安定させる。35秒で1.177 m前進、横ずれ0.056 m、転倒なしを確認した。これはシミュレーション上の基準値であり、実機性能の保証値ではない。

## 空圧特性の意味

`config/pneumatic_sim.json` の各関節には次の値がある。

| 値 | 単位 | 実機での求め方 |
|---|---:|---|
| `delay_s` | s | 指令変更から角度が動き始めるまで |
| `extend_speed_rad_s` | rad/s | 伸長方向のほぼ一定速度区間の傾き |
| `retract_speed_rad_s` | rad/s | 収縮方向のほぼ一定速度区間の傾き |
| `time_constant_s` | s | 目標近傍で最終変位の63%に達する時間の目安 |
| `deadband_rad` | rad | 指令しても動かない最小角度差 |
| `kp_nm_rad`, `kd_nm_s_rad` | Nm/rad, Nms/rad | 荷重下の硬さと減衰を合わせる値 |
| `max_torque_nm` | Nm | シリンダ推力×リンク有効腕長から見積もる上限 |

初期値は脚ごとの差を意図的に持たせた仮値である。実機同定後はJSONだけを変更し、制御コードは共通のまま使う。

## 実機同定の順序

1. ロボットを吊るか脚単体治具に固定し、接地荷重なしで試験する。
2. 各軸を中立値から安全な小振幅（最初は全レンジの5%程度）だけ正負にステップさせる。
3. 目標値、実測位置、弁指令、圧力を100 Hz以上で記録する。
4. 伸長・収縮を別々に3回以上測り、中央値から `delay_s` と速度を設定する。
5. 接地荷重を加え、`kp`、`kd`、最大トルク相当値を調整する。
6. シミュレーションでは `stride_m=0.02`、`lift_m=0.02` 程度から開始し、転倒しない範囲で拡大する。
7. 実機では適応を最初は無効にし、固定歩容を低圧・吊り状態で確認してから有効化する。

適応器は目標速度と追従誤差から、軸ごとの遅れを位相進み時間としてオンライン学習する。学習値は `0..max_lead_s` に制限し、元の関節制限も越えない。これは未知の空圧差をすべて補償する万能制御ではないため、圧力低下、漏れ、接地センサ異常、姿勢異常の安全停止は実機側で引き続き必要である。

## 実機連携の現状

シミュレーション由来の軌道は8列CSVへ変換済みで、`Motion/Fixed Motion/rabbit_bound.csv`
(`# generated_from=mujoco_rabbit_bound`、軸別先行 `phase_advance_frames` 焼き込み)が実機で
使われている。ただしシミュレータのradと実機0〜4095指令の対応は3Dプレビュー値による暫定線形
対応のままである。`python scripts/robotctl.py characterize` で軸別のむだ時間・速度を実測し、
`config/pneumatic_sim.json` の仮値を実測値へ置き換えるのが次の課題(校正曲線ベースのCSV生成は
その後)。
