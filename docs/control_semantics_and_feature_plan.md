# Control Semantics And Feature Plan

## 1. 目的

このドキュメントは、以下をまとめた設計メモです。

- 現行システムにおける `Position / Voltage / Command / Offset / Stroke` の意味
- ESP32 側コードを確認したうえでの整合確認
- ブラウザベースへのリファクタリング時に反映すべき命名とUI方針
- 追加要望の実装計画

確認対象のESP32側実装:

- Repository: `oriCylinder/pneumatic-quadruped-leg`
- Branch: `feature_SerialPID_SEM`
- 主に確認したファイル:
  - `main/main.ino`
  - `main/PID.cpp`
  - `main/DataPollAndParse.cpp`

## 2. 用語の意味

### 2.1 Voltage

- 意味: 生のセンサ入力値
- 実体: `analogRead(sensorPins[i])` の戻り値
- 位置補正前の値
- 12bit ADC 値として扱われている

設計上の理解:

- `Voltage` は物理位置そのものではない
- センサ電圧をADCで読んだ生値
- UI上では `Raw Sensor` または `Raw ADC` と表示した方が意味が伝わりやすい

### 2.2 Position

- 意味: 補正済みの位置値
- 実体: `Voltage` を `Offset` と `Stroke` で補正し、`0..4095` にマッピングした値

ESP32側では次の流れになっている。

- 生値 `getValtageAry[i]` を読む
- `capturedValAry[i][1]` と `capturedValAry[i][0]` を使って `map(...)`
- `0..4095` に `constrain(...)`
- `posAry[i][1]` に保存

設計上の理解:

- `Position` は制御に使う現在位置
- 生センサ値ではなく、校正後の論理的な位置量
- UI上では `Calibrated Position` と表示すると明確

### 2.3 Command

- 意味: バルブへの出力コマンド
- 実体: サーボバルブへ与える角度指令に近い値

ESP32側では:

- 位置制御モード時:
  - `commandAry[i] = vCommand[i].calcCommand(targetPosition, currentPosition);`
- 直接コマンドモード時:
  - 受信値を `float(dataAry[i + 4]) / 10.0 - 90.0` に変換
- 最終的に:
  - `valve[i].write(commandAry[i] + 90.0);`

設計上の理解:

- `Command` は抽象的な制御量ではなく、かなり直接的にバルブサーボの駆動量
- テレメトリでは `0..1800` 系の表現で返している
- `900` が中立、`0` と `1800` が両端

UI上では:

- `Output Command`
- `Valve Command`

のように表示すると、目標位置と区別しやすい

## 3. Offset と Stroke の意味

### 3.1 Offset

- 意味: ゼロ側のキャプチャ値
- 実体: `capturedValAry[i][1]`
- NVSキー: `oCapVal`

解釈:

- 原点側、またはエアーが抜けた状態などの基準位置を読むための値
- UIでは `Zero Capture` と説明すると分かりやすい

### 3.2 Stroke

- 意味: ストローク終端側のキャプチャ値
- 実体: `capturedValAry[i][0]`
- NVSキー: `sCapCal`

解釈:

- 可動域の上限側を取る校正値
- UIでは `Span Capture` または `Stroke End Capture` と説明するとよい

### 3.3 Position 補正との関係

現在位置は概念的には以下の計算で得られている。

```text
raw sensor voltage
  -> map(raw, offset, stroke, 0, 4095)
  -> calibrated position
```

つまり:

- `Voltage` = 未補正の観測値
- `Offset / Stroke` = 校正用の両端基準
- `Position` = 補正済み位置

## 4. 現時点の理解まとめ

現行システムの思想は以下で整理できる。

```text
Raw Sensor (Voltage)
  -> calibration (Offset / Stroke)
  -> Calibrated Position
  -> PID
  -> Output Command
  -> Valve Servo
```

加えて、手動操作として:

```text
Direct Command
  -> Output Command
  -> Valve Servo
```

のバイパスがある。

## 5. リファクタリング時の命名方針

### 5.1 API / Model 推奨

現状:

- `telemetry.position`
- `telemetry.voltage`
- `telemetry.command`
- `target_position`
- `target_command`

推奨:

- `telemetry.raw_sensor`
- `telemetry.position`
- `telemetry.output_command`
- `targets.position`
- `targets.command`
- `calibration.offset_capture`
- `calibration.stroke_capture`

### 5.2 UI 表示名推奨

- `Voltage` -> `Raw Sensor`
- `Position` -> `Calibrated Position`
- `Command` -> `Valve Command`
- `Target Position` -> `Position Target`
- `Target Command` -> `Direct Valve Command`
- `Offset` -> `Zero Capture`
- `Stroke` -> `Stroke Capture`

## 6. 追加要望

以下の追加要望がある。

### 6.1 Command バーの最大値見直し

要望:

- 現在 Command バーが `4096` 近くまである
- 実際のバルブ値は `1800` 系なので、最大値を `1800` にしたい

理解:

- ESP32側の出力表現は `0..1800`
- `900` が中立
- 現状の `0..4095` は Position 系のレンジであり、Command 系にそのまま使うのは不適切

対応方針:

- Web UI の `Target Command` 入力レンジを `0..1800` に変更
- 中立値 `900` を明示表示
- APIバリデーションも `command` モードでは `0..1800` に制限する
- ダミーESPの初期 `Command` / `Target Command` も `900` に合わせる

### 6.2 フリーモード

要望:

- バルブの `900` が真ん中
- `0` にすると手で動かせる
- フリーボタンでコマンドを `0` にして、手で動かせるようにしたい
- ただしエアーが切れているとき限定なので扱いを考える必要がある

理解:

- `command=0` はバルブの片側端で、空気の流れが止まる、または自由に動かしやすい状態になる前提
- これは安全条件に依存する
- ソフトだけで常に安全とは言い切れない

推奨方針:

- ソフト側は `Free Mode Request` を送れるようにする
- ただし UI 上は以下のいずれかでガードする

候補:

- `Air Off Confirm` トグルがONのときだけ Free ボタンを有効化
- 押下時に確認ダイアログを出す
- 将来、圧力センサやエアー供給状態が取得できるようになったら、その値で自動判定

### 6.3 ダイレクトティーチング

要望:

- Start を押す
- Free 状態で手で動かしてモーションを作る
- Stop まで録画
- その軌道を再現したい

理解:

- Free 中の `Position` を時間付きで連続サンプリング
- Stop 時に CSV 相当のデータ列として保持
- 再生時に既存のCSV再生機構へつなげられる可能性が高い

基本方針:

- Direct Teaching は新規独立機能ではなく、`recorded motion -> CSV playback compatible data` として設計する
- そうすると既存の `set_target_values_bulk` や再生機構と統合しやすい

### 6.4 関節全体管理画面

要望:

- hip と knee がセットなので、全体の関節を管理またはモニタできる画面がほしい

理解:

- 現在はアクチュエータ単位の監視が中心
- 実運用上は脚単位、関節ペア単位の方が理解しやすい

推奨方針:

- アクチュエータ表示に加えて、脚単位の grouped view を作る
- 例:
  - Front Right: hip, knee
  - Front Left: hip, knee
  - Rear Right: hip, knee
  - Rear Left: hip, knee

### 6.5 圧力表示 UI

要望:

- 各軸の圧力を kPa で8本表示できるUI
- 通信要件は未実装なのでUIモックだけ先にほしい

理解:

- 現段階では backend / ESP32 から圧力データは来ない
- ただし UI の場所とレイアウトは先に設計可能

推奨方針:

- ダッシュボードに `Pressure (Mock)` パネルを追加
- 8軸分のカードを表示
- 値は `-- kPa` あるいはダミー値
- 「通信未接続」を明記

## 7. 実装計画

### Phase 1: 用語と制約の是正

目的:

- 誤解を生みやすい部分を先に整える

対象:

- Command 系レンジを `0..1800` に修正
- UIラベルを意味に沿って修正
- `Command` と `Target Command` を分離して表現
- `Position / Voltage / Command` の説明文をUIに追加

成果物:

- Web UI ラベル更新
- API バリデーション調整
- 設計メモ更新

### Phase 2: Gain / Capture UI

目的:

- 現行 Native GUI にある校正系機能を Web UI に移す

対象:

- Gain request
- Gain set
- Gain save
- Offset capture
- Stroke capture
- Capture min / max の表示

成果物:

- アクチュエータカードに `Calibration` セクション追加
- `Gain` と `Capture` の REST 操作追加または既存APIの利用

### Phase 3: Free Mode

目的:

- 手で動かせる状態へ切り替える機能を Web UI から使えるようにする

仕様案:

- Free ボタン押下で `command=0` を送る
- 全軸 Free と単軸 Free の両方を検討
- UI上で中立 `900` と Free `0` の意味を明示

安全設計案:

- `Air Off Confirmed` チェックがないと押せない
- 実行時に確認ダイアログ
- 将来は圧力や供給状態の検出で制御

### Phase 4: Direct Teaching

目的:

- 手で作ったモーションを記録・再生する

仕様案:

- Start:
  - Free モードへ移行
  - 現在時刻から記録開始
- Recording:
  - 8軸の `Position` を一定周期で記録
- Stop:
  - 記録終了
  - その場で再生可能なモーションとして保存

データ形式案:

- 既存CSV形式と互換の配列
- 1行 = 1フレーム
- 8列 = 8軸 target position

懸念:

- Free 中は本当に手で動かせる条件が必要
- サンプリング周期と再生周期を揃える必要あり
- hip / knee の相関を可視化したい

### Phase 5: 全体関節管理画面

目的:

- 軸単位でなく脚単位で状況把握できるようにする

仕様案:

- `Actuator View`
  - 既存の軸単位カード
- `Leg View`
  - Front Right / Front Left / Rear Right / Rear Left
  - 各脚に hip / knee をまとめる

表示項目案:

- Position
- Raw Sensor
- Valve Command
- Target Position
- Target Command
- Gain summary
- Capture summary

### Phase 6: 圧力表示モック

目的:

- 今後の圧力通信に備え、UI構造を先に固める

仕様案:

- 8軸分の `Pressure` カード
- `-- kPa` 表示
- 状態タグ: `Mock`
- 将来、`telemetry.pressure_kpa` が来たらそのまま差し替える

## 8. 優先順位

推奨実装順:

1. Command レンジ修正と用語整理
2. Gain / Capture UI
3. Pressure UI モック
4. 脚単位ビュー
5. Free Mode
6. Direct Teaching

理由:

- まずは現行意味づけの誤差を減らす方が安全
- 次に既存機能を Web UI へ移す
- Free と Direct Teaching は便利だが、安全条件の整理が必要

## 9. 未確定事項

以下は実装前に決める必要がある。

- `Free Mode` を単軸で許すか、全軸同時だけにするか
- エアーOFFの条件をソフトでどう扱うか
- `Direct Teaching` のサンプリング周期を何Hzにするか
- 記録データをCSVとして保存するか、JSONモーションとして保存するか
- 圧力データの通信仕様をどうするか

## 10. 現時点の推奨

現時点では次の方針が最も安定している。

- セマンティクスは以下で確定扱いにしてよい
  - `Voltage = raw sensor`
  - `Position = calibrated position`
  - `Command = valve output command`
  - `Offset / Stroke = calibration endpoints`
- Web UI は `観測 / 制御 / 校正` の3分割で設計する
- `Direct Teaching` は単独実装ではなく、既存のモーション再生機構へ接続する設計にする
- `Free Mode` は安全確認ありの機能として実装する
