# BMX055 IMU 制御統合・最適化 計画書

作成日: 2026-07-03
対象ブランチ: `codex-imu-fusion-webapp`(main へのマージは全フェーズ完了後)

---

## 1. 背景と現状分析

### 1.1 現状の結論: IMU は完全に「表示専用」

調査の結果、IMU(BMX055)は制御に一切寄与していない。

- `SensorService`(IMU 側)と `ControlService`(アクチュエータ制御側)は `main.py:37-38` で独立に生成され、**相互参照がゼロ**。IMU データの流れは「センサ → WebSocket → ブラウザ表示」のみ。
- 制御経路は `POST /api/actuators/{id}/target` → `ControlService.set_target()`(`control_service.py:266-286`)→ シリアルフレーム → ESP32 の一方通行。
- 唯一の閉ループは CSV 再生時の行送りガード(`_wait_for_row_ready`, `control_service.py:440-487`)だが、これは**アクチュエータ位置/圧力**のフィードバックであり IMU は無関係。

### 1.2 GUI への寄与

- フュージョン結果(クォータニオン)は 20Hz で `sensor_state` イベントとして WebSocket 配信され、Vue の 3D ビューポート(`RobotModelViewport.vue`)でロボットモデルを傾ける + `SensorCalibrationPanel.vue` の数値表示に使われている。
- **表示上の問題**: クォータニオンを three.js シーンにそのまま適用しており(`applyImuOrientation()`, L215-234)、IMU 座標系 → three.js シーン座標系の変換が無い。表示姿勢の軸が実機と一致しない可能性が高い。

### 1.3 現状コードの主な課題

| # | 課題 | 場所 | 影響 |
|---|------|------|------|
| 1 | 更新レート 20Hz(`sensor_poll_interval_sec=0.05`) | `config.py:38`, `sensor_service.py:280-284` | Mahony kp=1.2 に対して低すぎ、制御用途には不足 |
| 2 | 読み取りが毎サンプル `asyncio.to_thread` | `sensor_service.py:280-284` | 100Hz 化するとスレッドプール往復のオーバーヘッドが支配的に |
| 3 | 磁気センサが**無較正の生値**でフュージョンに投入 | `sensor_service.py:_read_once` | ヨーが信頼できない(ハード/ソフトアイアン誤差) |
| 4 | Mahony 積分項が死にコード(ki=0 で毎回ゼロクリア) | `attitude.py:173-176` | ジャイロバイアスのオンライン推定が働かない |
| 5 | 較正ファイル I/O が async 内で同期実行 | `sensor_service.py:199-215` | イベントループの微小ブロック |
| 6 | 実機パスとエミュレートパスのロジック重複 | `sensor_service.py:286-336` vs `374-451` | 修正漏れの温床 |
| 7 | `sensor_state` だけ GUI 側 40ms フラッシュバッファを迂回 | `control.ts:272-274` | IMU 更新のたびに three.js 全リンク走査+再描画 |
| 8 | WebSocket ブロードキャストが直列 await | `websocket_manager.py:30-34` | 複数クライアント時に配信遅延が累積 |
| 9 | 3D 表示の座標系変換なし | `RobotModelViewport.vue:215-234` | 表示姿勢が実機と不一致の可能性 |
| 10 | `temperature_c` が常に None(ドライバ未実装のままモデル/UI に配管) | `imu_bmx055.py:66-73` | 死にデータ |

---

## 2. 目標(ユーザー確認済み)

1. **姿勢フィードバック制御**: IMU のロール/ピッチで胴体を水平に保つ閉ループ制御(ランタイムで ON/OFF 可能、デフォルト OFF)
2. **モーション補正**: CSV モーション再生中に IMU 姿勢で目標値を補正
3. **IMU 品質**: 読み取り~100Hz 化、磁気較正、フィルタ調整まで全部実施
4. **GUI**: 座標系修正 + 性能改善 + 制御状態の可視化

---

## 3. アーキテクチャ方針

```
┌─ 専用IMUスレッド (~100Hz) ────────────────────┐
│ Bmx055Reader.read() → 較正適用 → MahonyMARG    │
│ → AttitudeState (スレッドセーフな最新値共有)    │
└──────┬──────────────────────┬─────────────────┘
       │ 最新値参照(lock付き)  │ 10-20Hzに間引き
       ▼                      ▼
┌─ StabilizationController ─┐  ┌─ SensorService._publish ─┐
│ 20-50Hz asyncioループ      │  │ WebSocket sensor_state    │
│ roll/pitch誤差 → PID       │  │ (表示用、従来互換+追加)    │
│ → 脚ジオメトリ写像          │  └──────────────────────────┘
│ → per-actuator 補正値      │
└──────┬────────────────────┘
       ▼
ControlService (補正合成: user目標 or CSV目標 + correction)
       ▼
SerialGateway → ESP32 ×2 → 空圧アクチュエータ ×8
```

設計上の要点:

- **100Hz 読み取りは専用スレッド**で回す(`asyncio.to_thread` 毎サンプルは廃止)。最新姿勢は lock 付きの共有ステート(`AttitudeState`)に書き、asyncio 側は参照するだけ。
- **制御レートとセンサレートを分離**: フュージョンは 100Hz、アクチュエータへの補正送信は 20-50Hz(シリアル帯域と空圧応答の遅さに合わせる。空圧は応答が遅いので 100Hz 制御は無意味かつ有害)。
- **補正は加算合成**: 最終目標値 = ユーザー/CSV 目標値 + stabilization 補正値(クランプ付き)。既存の `set_target` 経路を壊さない。
- **WebSocket 配信は 10-20Hz に間引き**(表示にはそれで十分)。メッセージ形状は追加フィールドのみ(後方互換)。

---

## 4. 実装フェーズ

### Phase 1: IMU パイプライン高速化とフュージョン品質改善(バックエンド基盤)

**目的**: 制御に耐える 100Hz の姿勢推定を作る。制御には未接続なので単独でテスト・検証可能。

#### 1-1. BMX055 レジスタ設定の見直し — `sensors/imu_bmx055.py`
- 加速度計 ODR を 125Hz → 250Hz 以上(BW レジスタ)、ジャイロ ODR/BW を 100Hz 読み取りに十分な設定(例: 200Hz ODR / 64Hz BW)に変更。現行設定値をデータシートと突き合わせて確認すること。
- 磁気センサ(BMM150)は最大 ~30Hz なので、**磁気だけ低レートで読み、加速度+ジャイロは 100Hz** の非対称読み取りにする(Mahony は磁気更新が疎でも動く。磁気が無い周期は 6 軸更新にフォールバック)。
- 温度読み取りを実装するか、`temperature_c` の配管を削除する(どちらでも可、残すなら実装)。

#### 1-2. 専用読み取りスレッド — `sensors/sensor_service.py` 改修
- 新クラス `ImuPipeline`(または `SensorService` 内部再構成):
  - `threading.Thread` で `~100Hz` ループ: read → gyro バイアス減算 → mag 較正適用 → `MahonyMARG.update(dt)` → 共有 `AttitudeState` 更新。
  - `AttitudeState`: `threading.Lock` 保護の dataclass(quaternion, euler, gravity, linear_accel, gyro_dps, timestamp, sample_count)。`snapshot()` で安全にコピー取得。
  - 停止は `threading.Event`。開始/停止は既存の lifespan に統合。
- `_poll_loop`(asyncio 側)は「共有ステートを 10-20Hz で読んで `_publish` する」だけに縮小。
- ADC(MCP3204)は現行レートのままで良い(asyncio 側で継続)。
- **実機/エミュレート重複の解消**: 読み取り源を `ImuSource` プロトコル(`RealImuSource` / `EmulatedImuSource`)に抽象化し、較正適用・フュージョン・状態構築のロジックを一本化する。
- 較正ファイル I/O を `asyncio.to_thread` 化(`_save_imu_calibration`)。

#### 1-3. 磁気センサ較正 — 新規 `sensors/mag_calibration.py` + API
- ハードアイアン(オフセット)+ ソフトアイアン(スケール対角近似で可、フル楕円体フィットは任意)較正。
- 収集フロー: `POST /api/sensors/imu/calibration/mag/start` → ユーザーがロボットを各方向に回す間サンプル収集(30-60 秒、進捗を `sensor_state` に載せる)→ `.../mag/finish` でフィット・保存。既存 `ImuCalibration` モデルと `config/imu_calibration.json` に `mag_offset` / `mag_scale` を追加。
- 較正品質指標(フィット残差、カバレッジ)を返す。

#### 1-4. フィルタ調整 — `sensors/attitude.py`
- 100Hz 前提で kp を再調整(目安: kp=0.5-1.0)、**ki を有効化**(0.01-0.05)しジャイロバイアスのオンライン補正を生かす。ki<=0 時に integral をゼロクリアする挙動(L173-176)を修正。
- 磁気更新が無い周期の 6 軸(IMU-only)更新パスを追加。
- 静止検知(gyro ノルム閾値)での高速収束モード(初期化直後に kp を一時的に上げる)は任意。

#### 1-5. テスト
- `tests/test_attitude.py`(新規): 既知の回転系列に対する Mahony 収束、6 軸フォールバック、ki 有効時のバイアス推定。
- `tests/test_sensor_service.py` 拡張: 100Hz スレッドの起動/停止、`AttitudeState.snapshot()` の整合、エミュレートモード互換。
- 磁気較正のフィット単体テスト(合成データで楕円体 → 補正後の球形性を検証)。

**完了条件**: エミュレートモードで 100Hz フュージョンが回り、WebSocket 配信は 10-20Hz、既存 GUI 表示が壊れていない。実機では `--demo` なしで同様に動作(実機検証はユーザー)。

---

### Phase 2: 姿勢フィードバック制御(スタビライゼーション)

**目的**: ロール/ピッチ誤差を脚のアクチュエータ補正値に変換する閉ループ。デフォルト OFF、API で ON/OFF。

#### 2-1. 制御則モジュール — 新規 `application/stabilization.py`
- `StabilizationController`:
  - 入力: `AttitudeState.snapshot()`(roll/pitch、レベル較正適用済み)
  - 制御則: 軸別 PID(まず PD で開始、I は定常偏差補正用に小さく)。`roll_error = 0 - roll`, `pitch_error = 0 - pitch`。
  - **脚ジオメトリ写像**: roll/pitch 補正モーメント → 4 脚(8 アクチュエータ)への配分行列。`application/joint_preview.py` と URDF(`pql-a00_description/`)から脚配置(前後左右)を確認し、静的な混合行列 `M (8×2)` として実装(例: 右側の脚を伸ばす=左ロール補正)。**実機の脚とアクチュエータの対応はコードだけでは確定できない可能性があるため、混合行列は config で上書き可能にする。**
  - 出力: per-actuator 補正値(位置モードのオフセット)。
- **安全機構(必須)**:
  - 補正値クランプ(config: `stabilization_max_correction`)
  - 補正値レートリミッタ(空圧の応答に合わせる)
  - 自動無効化: 傾き閾値超過(例: 30°)、IMU データ鮮度切れ(例: 200ms 以上更新なし)、シリアル送信失敗連続時
  - 無効化時は補正をゼロへスムーズに戻す(即ゼロで脱力ショックを避ける)
- ループ: asyncio タスクで 20-50Hz(config: `stabilization_rate_hz`、デフォルト 25Hz)。シリアル帯域: 1 周期あたり 2 ポート × set_target フレームで済むようバッチ送信を検討(`frames.py` のフレーム仕様を確認し、4 アクチュエータ一括フレームがあるなら利用)。

#### 2-2. ControlService への合成 — `application/control_service.py`
- `set_target` 系に「ベース目標値」と「補正値」を分離保持する層を追加: `effective_target = clamp(base_target + correction)`。
- ユーザーが手動で目標変更した場合もベース値のみ更新し、補正は継続合成。
- stabilization OFF 時は従来と完全に同一動作(回帰ゼロ)。

#### 2-3. API — `api/routes.py` + `domain/models.py`
- `GET /api/control/stabilization` → `StabilizationState`(enabled, gains, per-actuator corrections, attitude error, auto-disable 理由, rate)
- `POST /api/control/stabilization` → enable/disable + ゲイン設定(`StabilizationRequest`)
- WebSocket に `stabilization_state` イベント追加(~10Hz、または `server_status` に同梱)。ゲインは `config/stabilization.json` に永続化(imu_calibration.json と同パターン)。

#### 2-4. テスト
- `tests/test_stabilization.py`(新規): 混合行列の符号(ロール正 → どのアクチュエータが正補正か)、クランプ、レートリミット、自動無効化(傾き超過・鮮度切れ)、有効/無効遷移のスムーズさ。
- エミュレートモード E2E: エミュレータに傾きを注入 → 補正値が出る → 傾きゼロで補正が収束、を統合テスト化。

**完了条件**: エミュレートモードで傾き注入 → 正しい向きの補正が出て安全機構が全て発火することをテストで実証。**実機でのゲイン調整は別途ユーザー立ち会いで実施(初期ゲインは極小に設定)。**

---

### Phase 3: モーション補正(CSV 再生との統合)

**目的**: CSV モーション再生中も姿勢補正を効かせ、姿勢崩れ時は再生を保護する。

- `_apply_csv_row`(`control_service.py`)が設定する目標値も Phase 2 の「ベース目標値」経路を通し、補正が自動合成されるようにする(Phase 2 の設計が正しければ追加実装は薄い)。
- 行送りガード拡張: `_row_ready` に姿勢条件を追加(オプション、config: `playback_attitude_guard_deg`)。傾きが閾値超過なら行送りを保留(既存の位置/圧力ガードと AND)。
- `playback_guard` イベントに姿勢起因の保留理由を追加。
- テスト: 再生中の補正合成、姿勢ガードによる行送り保留/再開。

**完了条件**: エミュレートモードで「再生中に傾き注入 → 補正+行送り保留 → 復帰で再開」が通る。

---

### Phase 4: GUI 最適化と制御可視化

**目的**: 表示の正しさ・軽さ・制御状態の見える化。

#### 4-1. 座標系修正 — `RobotModelViewport.vue`
- IMU ボディ座標系(Mahony 出力。X 前方/Y 左/Z 上か、`attitude.py` の重力符号から規約を確定)→ three.js シーン座標系(camera.up=(0,0,1))への**固定基底変換クォータニオン**を定義し、`applyImuOrientation()` で `q_scene = q_transform * q_imu * q_transform⁻¹` を適用。
- 軸の対応・符号は config かコンポーネント定数で上書き可能に(実機との突き合わせで反転が必要になりがち)。
- 検証: エミュレータの既知姿勢(ロール +10° 等)と 3D 表示の傾きが一致すること。

#### 4-2. 性能改善
- `control.ts`: `sensor_state` を `pendingSensors` バッファ経由にし、既存の 40ms フラッシュ(`UI_FLUSH_INTERVAL_MS`)に統合(three.js 再描画 ≤25fps 化)。
- `RobotModelViewport.vue`: 姿勢適用(`robotRoot.quaternion` 設定 + render)を `applyPose()` から分離し、IMU 更新では**メッシュ全走査(`applyFocusedHighlight`)を再実行しない**。
- `websocket_manager.py`: ブロードキャストを `asyncio.gather` 並列化 + 切断ソケットの掃除。
- 初期ロードの二重取得(REST refresh + WS snapshot)は低優先(時間があれば WS snapshot 到着時に REST 側をスキップ)。

#### 4-3. 制御可視化 UI
- 新コンポーネント `StabilizationPanel.vue`(配置: Sensor Calib タブを「Sensors & Control」に拡張、またはダッシュボードにカード追加 — 既存 PrimeVue Card/Tag/ToggleSwitch パターンに従う):
  - ON/OFF トグル(確認ダイアログ付き — 実機が動くため)
  - ゲイン入力(P/I/D per axis)
  - 姿勢誤差(roll/pitch)のライブ表示
  - per-actuator 補正値の一覧(符号と大きさ)
  - 自動無効化ステータス(理由をタグ表示: 傾き超過/センサ停止 等)
- 磁気較正 UI: `SensorCalibrationPanel.vue` に開始/終了ボタン + 収集進捗バー + 較正品質表示を追加(既存の gyro-zero ボタンの実装パターンに従う)。
- `types/control.ts` / `controlApi.ts` にバックエンド追加分の型・API クライアントを同期。
- モバイルレイアウト(≤820px、日本語ラベル)を踏襲。

#### 4-4. 検証
- `vue-tsc --noEmit` + ビルド通過。
- `--demo`(エミュレート)+ Vite dev server + Playwright MCP で: 姿勢表示の軸一致、トグル操作 → 補正表示、磁気較正フローの UI 動作を確認。

---

### Phase 5: 統合検証・レビュー・マージ準備

1. 全テストスイート(`pytest`)+ 新規テスト、カバレッジ 80% 以上(新規コード)。
2. エミュレートモードでのエンドツーエンド通し確認(`python -m highend_server --demo`)。
3. コードレビュー: `fastapi-reviewer` / `python-reviewer` / `typescript-reviewer` エージェントで並列レビュー、CRITICAL/HIGH を修正。
4. ドキュメント更新: `readme.md` / `docs/raspberry_pi_setup_tutorial.md` に stabilization 設定・磁気較正手順・新 config キーを追記。
5. コミット整理(conventional commits)→ main への PR 作成。
6. **実機検証はユーザー実施**: ゲイン初期値は安全側(極小)、実機での軸符号確認チェックリストを docs に用意。

---

## 5. リスクと対策

| リスク | 対策 |
|--------|------|
| 空圧アクチュエータの応答遅れで発振 | 制御レートを 25Hz 程度に抑制、PD 主体でゲイン極小から、レートリミッタ必須 |
| 混合行列(脚⇔アクチュエータ対応)の符号誤り | config で上書き可能に + 実機確認チェックリスト + 補正クランプで被害限定 |
| 100Hz スレッドと asyncio の競合 | 共有は `AttitudeState` の lock 付き snapshot のみに限定、スレッドから asyncio オブジェクトに触らない |
| シリアル帯域圧迫(補正送信で既存コマンドが遅延) | 補正送信は 25Hz にバウンド、可能ならバッチフレーム利用、送信キュー監視 |
| I2C 帯域(100Hz × 3 デバイス読み取り) | 磁気を低レート化、smbus2 のブロック読み取りで転送最小化。届かなければ 50Hz に緩和(それでも現状の 2.5 倍) |
| 座標系規約の誤り(表示・制御両方に波及) | Phase 1 で規約を 1 箇所(`attitude.py` docstring)に明文化し、全モジュールがそれを参照 |
| 磁気較正なしでのヨー暴れが制御に混入 | 姿勢制御はロール/ピッチのみ使用(ヨーは表示専用)と明確に線引き |

---

## 6. 実行体制(オーケストレーション)

指揮・計画・レビュー統括はメインセッションが担当し、実装は Sonnet/Opus サブエージェントに委譲する。

| フェーズ | 担当エージェント | モデル | 並列性 |
|----------|-----------------|--------|--------|
| Phase 1(IMU パイプライン) | general-purpose | **opus**(スレッド設計・フィルタ数学が繊細) | 単独 |
| Phase 2(制御統合) | general-purpose | **opus**(安全機構・制御則) | Phase 1 完了後 |
| Phase 3(モーション補正) | general-purpose | **sonnet** | Phase 2 完了後 |
| Phase 4(GUI) | general-purpose | **sonnet** | 4-1/4-2 は Phase 1 完了後に並行可、4-3 は Phase 2 の API 確定後 |
| 各フェーズ後レビュー | fastapi-reviewer / python-reviewer / typescript-reviewer | 既定 | 並列 |

- 各フェーズの完了条件(上記)をメインセッションが検証してから次フェーズへ。
- Phase 1 と Phase 4-1/4-2(GUI 性能・座標系)は依存が薄いため並行実行してリードタイム短縮。

## 7. 変更対象ファイル一覧(サマリ)

**バックエンド(新規)**: `sensors/mag_calibration.py`, `application/stabilization.py`, `tests/test_attitude.py`, `tests/test_stabilization.py`, `config/stabilization.json`(実行時生成)
**バックエンド(改修)**: `sensors/imu_bmx055.py`, `sensors/sensor_service.py`, `sensors/attitude.py`, `application/control_service.py`, `api/routes.py`, `api/websocket_manager.py`, `domain/models.py`, `config.py`, `main.py`
**フロントエンド(新規)**: `web-vue/src/components/StabilizationPanel.vue`
**フロントエンド(改修)**: `web-vue/src/stores/control.ts`, `web-vue/src/components/RobotModelViewport.vue`, `web-vue/src/components/SensorCalibrationPanel.vue`, `web-vue/src/services/controlApi.ts`, `web-vue/src/types/control.ts`, `web-vue/src/App.vue`
**ドキュメント**: `readme.md`, `docs/raspberry_pi_setup_tutorial.md`
