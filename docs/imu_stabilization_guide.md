# IMU 姿勢スタビライゼーション 運用ガイド

対象: BMX055 IMU による胴体水平維持(姿勢フィードバック制御)機能。

---

## 1. 概要

### やること

- IMU(BMX055 + Mahony フュージョン)が出す **Roll / Pitch** を使い、胴体を水平に保つ閉ループ制御を行う。
  - Roll/Pitch 誤差 → 軸別 PID → 脚(8 アクチュエータ)への位置補正値に変換 → ユーザー/CSV の目標値に加算合成してシリアル送信。
- **CSV モーション再生中の姿勢補正**にも対応する。再生中の目標値も同じ「ベース目標値 + 補正値」経路を通るため、スタビライゼーションが有効なら再生中も自動的に効く。
- オプションで、再生の行送りに **姿勢ガード**(`playback_attitude_guard_deg` / リクエストの `attitude_guard_deg`)を追加できる。傾きが閾値を超えている間は次の行に進まない(既存の位置/圧力ガードと AND 条件)。

### やらないこと

- **ヨー(Yaw)は制御に一切使わない。表示専用。** 磁気較正の精度に依存してヨーがドリフト・暴れることがあるため、姿勢制御は Roll/Pitch のみを見る設計になっている。
- スタビライゼーションはデフォルトで **常に OFF**。サーバー再起動時も自動では ON にならない(`config/stabilization.json` にはゲインと混合行列のみ保存され、`enabled` は保存されない)。

---

## 2. 安全機構の説明

すべて `application/stabilization.py` の `StabilizationController` に実装されている。

| 機構 | 内容 | 設定キー(既定値) |
|---|---|---|
| デフォルト OFF | 起動時・再起動後は常に無効。有効化は明示的な API 呼び出しのみ | ― |
| 補正クランプ | 各アクチュエータの補正値の絶対値を制限(位置単位、0..4095 スケール) | `stabilization_max_correction`(120.0) |
| レートリミッタ | 補正値の変化速度を制限(空圧アクチュエータの応答速度に合わせる) | `stabilization_max_correction_rate`(400.0 / 秒) |
| 傾き超過での自動無効化 | 水平較正後の \|Roll\| または \|Pitch\| がこの角度を超えると自動 OFF | `stabilization_max_tilt_deg`(30 deg) |
| 姿勢の鮮度切れでの自動無効化 | 最新の IMU 姿勢スナップショットがこの秒数より古いと自動 OFF | `stabilization_max_staleness_sec`(0.2 秒) |
| シリアル送信失敗での自動無効化 | 補正フレームの連続送信失敗がこの回数に達すると自動 OFF | `stabilization_serial_failure_limit`(5 回) |
| 内部エラーでの自動無効化 | 制御ループ内で例外が発生した場合も「internal error」として自動 OFF し、ループ自体は生かしたまま補正をゼロ方向に倒す。5 回連続で失敗した場合は直接ゼロ補正を強制送信する | ― |
| スムーズなランプゼロ | 無効化(手動・自動どちらも)の際、補正値は即ゼロではなく一定時間かけて滑らかにゼロへ収束する(脱力ショックを避ける) | `stabilization_disable_ramp_sec`(0.5 秒) |
| デッドバンド | 前回送信値からの変化がこの量未満なら同一ポートへの再送信を抑制 | `stabilization_correction_deadband`(4.0) |
| 積分アンチワインドアップ | 各軸 PID の積分項の蓄積量をクランプ | `stabilization_integral_limit`(40.0 deg・sec) |

補正値の符号規約: `+correction` はその脚を **伸ばす**(その角を持ち上げる)、`-correction` は **縮める**(下げる)。詳細は §4 のチェックリストと `application/stabilization.py` のモジュール docstring を参照。

---

## 3. 使い方

### 3.1 GUI(Sensors & Control タブ)

Web UI の「Sensors & Control」タブ(日本語表示では「センサ・制御」)内の **Stabilization** カードから操作する。

- **ON/OFF トグル**: ON にすると確認ダイアログ(「スタビライゼーションを有効化 / 実機のアクチュエータが動作します。有効化しますか?」)が出る。承認すると有効化される。OFF は確認なしで即座に無効化(補正はランプダウン)される。
- **ゲイン編集**: Roll / Pitch それぞれの Kp・Ki・Kd を入力し、「ゲインを適用」ボタンで送信する(入力中は自動送信されない)。
- **ライブ表示**: Roll/Pitch の実測値・誤差、ループレート(Hz)、自動無効化理由(タグ表示)、IMU データが古い場合の警告タグ、アクチュエータごとの補正値テーブルがリアルタイムに更新される。

### 3.2 REST API

```text
GET  /api/control/stabilization
POST /api/control/stabilization
```

`GET` はレスポンスとして `StabilizationState` を返す:

```json
{
  "enabled": true,
  "active": true,
  "auto_disabled": false,
  "disabled_reason": null,
  "gains": {
    "kp_roll": 1.5, "ki_roll": 0.0, "kd_roll": 0.3,
    "kp_pitch": 1.5, "ki_pitch": 0.0, "kd_pitch": 0.3
  },
  "roll_deg": 0.4,
  "pitch_deg": -0.1,
  "roll_error_deg": -0.4,
  "pitch_error_deg": 0.1,
  "corrections": [
    { "actuator_id": 0, "label": "Front-Right Hip", "correction": -3.2 }
  ],
  "loop_rate_hz": 25.1,
  "attitude_stale": false,
  "updated_at": "2026-07-07T00:00:00Z"
}
```

`POST` で ON/OFF とゲインを設定する(どちらも省略可、指定したものだけ更新される):

```json
{
  "enabled": true,
  "gains": {
    "kp_roll": 1.5,
    "ki_roll": 0.0,
    "kd_roll": 0.3,
    "kp_pitch": 1.5,
    "ki_pitch": 0.0,
    "kd_pitch": 0.3
  }
}
```

ゲインは `config/stabilization.json` に永続化される(`imu_calibration.json` と同じパターン)。ただし `enabled` は保存されない。

WebSocket でも `stabilization_state` イベントが配信され(状態遷移時は即時、有効時は約 8Hz)、`snapshot` イベントの `payload.stabilization` にも同じ形状で同梱される。

---

## 4. 較正手順

較正は次の順で行う: **水平較正 → ジャイロゼロ → 磁気較正**。

> **重要**: 較正 API(`level` / `gyro-zero` / `reset` / `mag/start` / `mag/finish`)は、**スタビライゼーションが有効(enabled または active)な間は 409 エラーで拒否される**。較正の前に必ずスタビライゼーションを OFF にすること(較正はフュージョンを一瞬 identity に近い状態へスナップさせるため、有効なままだと実機アクチュエータに PID の微分キックが入る)。`mag/cancel` のみ、この制限を受けない。

1. **水平較正**(`POST /api/sensors/imu/calibration/level`): ロボットを基準姿勢(直立)に置き、GUI の「現在姿勢を水平として保存」を押す。現在の Roll/Pitch を 0 度として保存する。
2. **ジャイロゼロ**(`POST /api/sensors/imu/calibration/gyro-zero`): ロボットを完全に静止させ、「静止ジャイロをゼロ保存」を押す。数秒間平均を取ってジャイロのドリフト分を保存する。
3. **磁気較正**(ハード/ソフトアイアン補正、ヨーの精度改善用):
   - `POST /api/sensors/imu/calibration/mag/start`(GUI: 「磁気較正開始」)でサンプル収集を開始する。
   - **ロボットを全方向にゆっくり回転させる(目安 30〜60 秒)**。GUI に進捗バーとサンプル数が表示される。
   - `POST /api/sensors/imu/calibration/mag/finish`(GUI: 「完了」)で楕円体フィットを実行し、結果を保存する。
   - 途中でやめる場合は `POST /api/sensors/imu/calibration/mag/cancel`(GUI: 「キャンセル」)。
   - 完了後、品質指標が表示される:
     - **サンプル数**: 収集したサンプル数
     - **残差**: フィット後の球形性からのズレ(小さいほど良い)
     - **カバレッジ**: 各方向への回転がどれだけ網羅されたか(0〜100%、高いほど良い)
4. すべて完了後、GUI 下部の「較正値」セクションで、水平補正角・ジャイロオフセット・磁気オフセット/スケールが期待どおりに保存されていることを確認する。
5. すべての較正をやり直したい場合は「IMU補正をリセット」(`POST /api/sensors/imu/calibration/reset`)で初期値に戻す。

---

## 5. 実機での軸符号確認チェックリスト(初回電源投入時、必ず実施)

較正が終わっても、**表示の傾きの向き・補正の伸縮方向が実機と一致している保証はない**。以下を必ず手順どおりに確認すること。ゲインは最初は極小のまま行う。

### Step 1: ロボットをスタンドに乗せる、または吊るす

脚が接地しない状態にする。誤った符号で有効化しても実際に転倒・衝突しないようにするため。

### Step 2: 3D 表示の傾きが実機の傾きと一致するか確認する

- ロボット本体を手で右に傾ける(右側を下げる)。
- Web UI の 3D モデル表示も同じ方向(右に傾く)に傾けば OK。
- **もし逆・別軸に傾く場合**: `web-vue/src/utils/imuFrame.ts` の `IMU_FRAME_ADJUST` を編集する。クォータニオン変換のコード自体(`imuQuaternionToScene`)は変更しないこと。`IMU_FRAME_ADJUST` の各エントリ(`x`/`y`/`z`)が、シーン側のその軸を IMU ボディ側のどの軸(`axis`)・符号(`sign`)から読むかを表しているので、ズレている軸のエントリだけを直す(例: Y が左右逆なら該当エントリの `sign` を反転)。

### Step 3: 最小ゲインでスタビライゼーションを有効化し、実際の脚の動きの符号を確認する

- ロール/ピッチのゲインを極小値(既定の Kp=1.5, Kd=0.3, Ki=0 程度、まずはさらに小さくしてもよい)のまま、GUI トグルで有効化する(確認ダイアログが出る)。
- 本体を手でゆっくり傾け(例: 右側を下げる)、各脚アクチュエータの補正値の符号を確認する。
  - **期待される動作**: 右側を下げた場合 → 右側の脚(Front-Right, Rear-Right)が **伸びる方向**(+correction)、左側の脚が縮む方向(-correction)に補正される。
  - ノーズアップ(前を持ち上げる)の場合 → リア側の脚(Rear-Right, Rear-Left)が伸び、フロント側が縮む方向に補正される。
  - デフォルトの混合行列(`application/stabilization.py` の `DEFAULT_MIXING_MATRIX`)はこの規約で組まれているが、**実機の脚とアクチュエータの配線・空圧配管まではコードから確定できない**ため、この確認は省略しないこと。
- **符号が逆だった場合**: `config/stabilization.json` に `mixing_matrix` を上書きして保存する。JSON の形は次のとおり(`gains` と同じファイルに同居する。`enabled` は保存対象外):

  ```json
  {
    "gains": {
      "kp_roll": 1.5, "ki_roll": 0.0, "kd_roll": 0.3,
      "kp_pitch": 1.5, "ki_pitch": 0.0, "kd_pitch": 0.3
    },
    "mixing_matrix": [
      [-1.0,  1.0],
      [-1.0,  1.0],
      [ 1.0,  1.0],
      [ 1.0,  1.0],
      [-1.0, -1.0],
      [-1.0, -1.0],
      [ 1.0, -1.0],
      [ 1.0, -1.0]
    ],
    "updated_at": "2026-07-07T00:00:00Z"
  }
  ```

  行はアクチュエータ ID 0〜7(既定の脚配置: 0-1 = Front-Right、2-3 = Front-Left、4-5 = Rear-Right、6-7 = Rear-Left)、列は `[roll係数, pitch係数]`。符号が逆になっている脚の行の値を反転させる。サーバー再起動(または次回のゲイン保存時の再読込)で反映される。

### Step 4: ゲインの調整

- Kp を小さい値から少しずつ上げていく。空圧アクチュエータは応答が遅いため、上げすぎるとゆっくりとした発振(ハンチング)が起きる。
- **発振が見られたら、Kp をこれ以上上げず、先に Kd を上げる。** Kd は角速度に応じたダンピングとして働き、遅い空圧系の発振を抑えるのに有効。Ki は基本 0(PD 制御)のまま運用し、定常偏差が気になる場合のみごく小さい値(0.02〜0.05 程度)を試す。
- 脚を接地させた実走行での最終調整は、スタンド/吊り下げでの符号確認が完全に終わってから行う。

---

## 6. 主要設定キー一覧

いずれも環境変数は `HIGHEND_` プレフィックス付き(例: `HIGHEND_STABILIZATION_MAX_TILT_DEG`)。定義元は `src/highend_server/config.py`。

### IMU / フュージョン

| キー | 既定値 | 単位 | 説明 |
|---|---|---|---|
| `imu_sample_rate_hz` | 100.0 | Hz | 専用スレッドでの加速度+ジャイロ読み取りレート |
| `imu_mag_sample_rate_hz` | 20.0 | Hz | 磁気センサ(BMM150)読み取りレート(100Hz は追従不可のため低レート) |
| `mahony_kp` | 0.8 | ― | Mahony フィルタの比例ゲイン(accel/mag 基準への収束の強さ) |
| `mahony_ki` | 0.02 | ― | Mahony フィルタの積分ゲイン(ジャイロバイアスのオンライン推定) |
| `mag_calibration_max_samples` | 2000 | 件 | 磁気較正収集バッファの上限サンプル数 |
| `sensor_poll_interval_sec` | 0.05 | 秒 | asyncio 側の ADC 読み取り/ジャイロゼロ収集のポーリング間隔 |
| `sensor_publish_interval_sec` | 0.05 | 秒 | WebSocket `sensor_state` 配信の間引き間隔 |

### スタビライゼーション

| キー | 既定値 | 単位 | 説明 |
|---|---|---|---|
| `stabilization_rate_hz` | 25.0 | Hz | 制御ループ(補正計算・送信)のレート |
| `stabilization_max_correction` | 120.0 | 位置単位(0..4095 スケール) | 補正値クランプの上限 |
| `stabilization_max_correction_rate` | 400.0 | 位置単位/秒 | 補正値の変化速度リミッタ |
| `stabilization_max_tilt_deg` | 30.0 | 度 | 自動無効化の傾き閾値(水平較正後の Roll/Pitch) |
| `stabilization_max_staleness_sec` | 0.2 | 秒 | 自動無効化の姿勢データ鮮度切れ閾値 |
| `stabilization_disable_ramp_sec` | 0.5 | 秒 | 無効化時の補正ゼロへのランプ時間 |
| `stabilization_serial_failure_limit` | 5 | 回 | 自動無効化に至るシリアル連続送信失敗回数 |
| `stabilization_correction_deadband` | 4.0 | 位置単位 | 変化が小さいときの再送信抑制しきい値 |
| `stabilization_integral_limit` | 40.0 | deg・sec | PID 積分項のアンチワインドアップ上限 |
| `stabilization_config_file_name` | `stabilization.json` | ― | `config/` 配下の永続化ファイル名 |

### CSV 再生の姿勢ガード(Phase 3)

| キー | 既定値 | 単位 | 説明 |
|---|---|---|---|
| `playback_attitude_guard_deg` | なし(`None`、無効) | 度 | 設定すると、水平較正後の \|Roll\|・\|Pitch\| がこの値を超える間、CSV 再生の行送りを保留する。リクエストごとに `CsvPlaybackRequest.attitude_guard_deg` で上書き可能 |

### ゲインの既定値(`StabilizationGains`、API/GUI から変更可能)

| キー | 既定値 |
|---|---|
| `kp_roll` | 1.5 |
| `ki_roll` | 0.0 |
| `kd_roll` | 0.3 |
| `kp_pitch` | 1.5 |
| `ki_pitch` | 0.0 |
| `kd_pitch` | 0.3 |

積分ゲインはデフォルト 0(純粋な PD)。起動直後のワインドアップを避けるため、P/D の挙動を実機で確認してから小さい Ki(0.02〜0.05 程度)を試すこと。

---

## 7. 実験の記録と再生(experiment/2026-07-11 以降)

### 実験の1コマンド操作(`scripts/robotctl.py`)

サーバー起動後、追加依存なし(標準ライブラリのみ)で使える操作系:

```bash
python scripts/robotctl.py preflight              # 環境・ハードウェアの事前点検
python scripts/robotctl.py status                 # サーバー/再生/記録/安定化の要約
python scripts/robotctl.py sensors                # IMUスナップショット
python scripts/robotctl.py stabilization-status   # ゲイン・補正値・D項モード

python scripts/robotctl.py experiment start manual-roll --name "Roll試験A"
python scripts/robotctl.py experiment note "ここで手で右に傾けた"
python scripts/robotctl.py experiment stop
python scripts/robotctl.py experiment list
python scripts/robotctl.py experiment show latest

python scripts/robotctl.py characterize --axis 0 --amplitude 300  # 軸ステップ応答同定
python scripts/walk_metrics.py metrics Logs/experiments/<ID>      # 歩行ラン解析
python scripts/pi_fetch_logs.py                                   # Piから最新ランを回収
```

リモートのPiに対しては `--host 192.168.x.x:8000` を付ける。安定化ONは意図的に含めていない。
**例外は `characterize` で、これはアクチュエータを実際に動かす**(既定±300、実行前に対話確認あり。
`--yes` で省略可だが、必ず脚を浮かせた状態で使うこと)。

### 実験ログの構造

`experiment start` ごとに `Logs/experiments/<YYYYMMDD_HHMMSS>_<type>/` が作られる:

- `manifest.json` — git SHA/ブランチ/dirty、全ゲイン、Mahony設定、D項モード、config全スナップショット。「このログのときKpいくつだっけ?」を根絶する
- `telemetry.csv` — 25Hz×8アクチュエータのlong format(59列: 基本41列+歩行/接地列)。各行で `base_target + round(stabilization_correction) == effective_target` が成立し、CSV再生の要求・IMU制御の上乗せ・実際の動きを1本の時系列で追える。IMU列(roll/pitch/yaw、gyro、accel、mag)と `accel_confidence_candidate`(|accel|-1g による high/medium/low、**記録のみ・制御非結合**)を含む
- `events.jsonl` — 安定化ON/OFF/自動無効化、再生イベント、較正、note を時刻付きで記録
- `notes.md` — 自由記入

時間軸は `elapsed_ms`(monotonic)が正。`timestamp` はNTP同期でジャンプし得る(PiはRTCなし)。既存の `/api/telemetry/recording` とは独立に併用可能だが、Pi上で両方を高レートで同時使用するのはSDカードIO的に非推奨。

### 仮想IMUシナリオ(実機なしで制御試験)

```bash
HIGHEND_EMULATED_IMU_SCENARIO=roll-step HIGHEND_EMULATE_DEVICES=true python -m highend_server
```

シナリオ: `smooth`(既定)/ `static` / `roll-step`(2〜5秒に+10°)/ `pitch-step` / `diagonal-step` / `impulse` / `oscillation`(0.5Hz)/ `gyro-bias` / `accel-disturbance`(姿勢0°のまま前方0.35g — Mahonyの並進誤認テスト)/ `sensor-stale`(鮮度切れ自動無効化の発火試験)/ `sensor-nan`(非有限値ガードの試験)

### ジャイロD項の比較(A/B実験用)

```bash
HIGHEND_STABILIZATION_DERIVATIVE_SOURCE=gyro_rate python -m highend_server
```

- `error_difference`(既定): D = 姿勢誤差の差分/dt(従来)
- `gyro_rate`: D項にジャイロレートを直接使用(Roll: -gyro_x / Pitch: -gyro_y)。フュージョン角の二重微分を避けノイズ増幅が小さい

使用モードは manifest.json と `stabilization-status` に記録されるため、翌日のログ比較で条件を取り違えない。

### 実機ログの再生(リモート開発用)

```bash
python -m highend_server --replay Logs/experiments/20260711_143522_roll_test/ [--replay-speed 2.0]
```

記録済みのIMU生データ(gyro/accel/mag)を**そのままMahonyフィルタに再入力**する。姿勢推定・IMU信頼度・制御量計算・ゲイン候補比較を、実機なしで同一入力に対して何度でも試せる(アクチュエータ応答は変わらないため完全なシミュレーションではない)。

注意: CSVはバイアス補正済みの値を保存しているため、`--replay` は較正の二重適用を防ぐために自動で空の一時configディレクトリを使う(手動で `HIGHEND_SENSOR_CONFIG_DIR_NAME` を設定した場合はそちらが優先)。
