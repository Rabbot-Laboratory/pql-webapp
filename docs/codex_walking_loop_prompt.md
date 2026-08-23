# Codex用プロンプト: PQL-A00 歩行達成までの実機反復ループ

以下をそのままCodexに貼り付けて使う。

---

## ミッション

空圧四足ロボットPQL-A00を**持続歩行(支持具なしで3周期以上、前進しながら転倒しない)**させる。
そのために「修正→デプロイ→実機テスト→ログ解析→次の修正」のループを、私(ユーザー)と協働で反復する。
**実機のボタン操作は私だけが行う。** あなたの役割は、テスト指示の明確化・ログ解析・修正・デプロイ。

## 環境

- ローカルリポジトリ: `C:\Users\MaedaNatsuki\Documents\Aircompressor_Robot`(ブランチ `experiment/2026-07-11`)
- 実機: Raspberry Pi(Tailscale経由)。接続情報は `config/pi_connection.json`(gitignore済み)を読むこと
- Pi側: `/home/rabbot/pql-webapp`、systemdサービス `highend-control.service`、GUI `http://<host>:8000/`
- Pi側の環境変数は `.env`(`HIGHEND_` プレフィックス、pydantic-settings)。サービス再起動で反映
- Pi側バックアップ: `~/deploy-backups/`(デプロイ前の状態を保存済み)

## ツール(すべて動作検証済み)

| 用途 | コマンド |
|---|---|
| デプロイ(md5差分同期+再起動) | `python scripts/pi_deploy.py --restart` |
| デプロイ内容の事前確認 | `python scripts/pi_deploy.py --dry-run` |
| 実験ログ回収 | `python scripts/pi_fetch_logs.py`(latest)/ `--list` / `<id部分文字列>` → `Logs/experiments/pi/` |
| ラン解析 | `python scripts/walk_metrics.py metrics <run_dir>` |
| ラン比較 | `python scripts/walk_metrics.py compare <run_A> <run_B>` |
| 軸ステップ応答同定 | `python scripts/robotctl.py --host <pi-host> characterize --axis N --amplitude 300`(**実機が動く。実行前に必ず私の許可を取る**) |
| テスト | `python -m pytest tests -q`(`tests/`のみ。single-leg-appは対象外) |
| lint | `python -m ruff check src tests scripts` |
| フロントビルド | `cd web-vue && npx vite build`(**`npm run build`は既存のvue-tscエラーで失敗するので使わない**) |

## 前提知識(これまでの結論 — 再調査不要)

8/11実機試験の確定原因(「歩きそうで歩けない」):
1. **軸別空圧遅れ0.20〜0.90sが非対称** → 後脚左右の蹴りが0.3〜0.45sズレて推進が死ぬ
2. **速度制限450unit/sが時間の36〜83%作動 + 0/4095機械端飽和** → 指令波形が実現されない
3. 前進量・滑りの計測なし → 「姿勢は安定するが進まない」を閉ループで直せない

2026-08-23実装済み(コミット c3c26cc / 1b089b9 / d940578 / c9c0053 / e7f05cb):
- サイクル指定歩行: 前進API(`POST /api/control/adaptive-walk/forward`)に `cycles`(1-10)と `mode`("adaptive"/"replay")。フル振幅後N周期で自動停止+実験自動記録。**自動停止後はボタンを一度離すまで再開拒否(409)**
- replayモード = 適応全OFFの素再生(ベースライン計測用)
- 実験CSV59列: 軸別の `walk_phase_offset / walk_attitude_offset / walk_phase_lead_s / walk_rate_limited / walk_saturated / walk_ilc_correction`、ティック別の `walk_phase / walk_cycle / walk_motion_scale`、接地4脚
- 位相進み学習のアンチワインドアップ(飽和・速度制限中は凍結)
- ILC(周期毎波形補正、悪化時自動巻き戻し): `HIGHEND_ADAPTIVE_WALK_ILC_GAIN`(既定0=無効)
- Raibertピッチ比例キック: `HIGHEND_ADAPTIVE_WALK_PITCH_THRUST_GAIN`(既定0=無効)
- 接地ゲート・支持脚別補正: `HIGHEND_ADAPTIVE_WALK_USE_CONTACT`(既定OFF)— **接地センサは使わない方針(ユーザー決定)。触らない**
- GUI: 歩行カード(モード/サイクル選択・軸別位相進みバー・飽和/速度制限フラグ)、ゲイトダイアグラム、実験記録パネル
- 歩容: `Motion/Fixed Motion/rabbit_bound.csv`(45フレーム×0.04s=周期1.8s、`joint_scale=0.65`、軸別先行 `phase_advance_frames=5,9,10,8,10,10,5,8` 焼き込み済み=v2)。**v2の実機ベースラインは未計測**
- 主要設定(config.py、env上書き可): `adaptive_walk_max_target_rate=1200`(送信側最終クランプ)、`adaptive_walk_max_phase_lead_s=0.20`、`adaptive_walk_motion_scale=1.0`、`adaptive_walk_max_tilt_deg=12`、周期はCSVの`interval_sec`
- 軸順(0-7): FR股, FR膝, FL股, FL膝, RR股, RR膝, RL股, RL膝。target増=シリンダ伸長
- 棄却済みアプローチ: RL・大規模MPC・ESKF置換・接地センサ依存の制御

## ループ手順(1イテレーション)

1. **テスト指示**: 私に実行してもらうランを1つだけ、GUI操作レベルで明確に指示(モード/サイクル数/事前姿勢)。**1イテレーションで変えるのは1変数だけ**
2. 私の「終わった」報告後: `pi_fetch_logs.py` → `walk_metrics metrics`(必要なら`compare`)。私の主観報告(進んだか・どう転んだか・音や滑りの様子)も必ず聞いて解析に組み込む
3. 症状→打ち手ガイド(下記)から**最小差分の修正**を決定。`pytest tests -q` 通過 → `git commit`(小さく) → `pi_deploy.py --restart`
4. 「何を変えたか・なぜ・期待する変化」を1〜3行で私に報告して次のテストへ

最初の3ラン(未実施なら必ずこの順で):
1. 素再生(replay)・1周期 → v2ベースライン
2. 素再生・3周期 → 周期再現性
3. 適応(adaptive)・3周期 → 適応層の寄与を compare で定量化

## 症状→打ち手ガイド

| 症状(metrics/主観) | 打ち手 |
|---|---|
| 後脚キック同期ズレ(rear kick sync delta)>0.1s | rabbit_bound.csv の軸別先行フレームを実測lagで再計算(lag差/0.04s≒フレーム差) |
| rate_limit_duty が高い(>30%) | CSV波形の傾きを緩和、または `max_target_rate` を+20%刻みで慎重に増(上限5000) |
| walk_saturated / actual 0-4095 張り付き | CSV振幅・中立オフセット調整(`joint_scale`再生成 or 該当軸の値域圧縮) |
| 位相進み学習が0.20s張り付き | CSV先行を増やす(学習で届く残差だけ残す)。`max_phase_lead_s`増は最後の手段 |
| Pitch RMSが周期毎に成長(バウンド発散) | 周期(interval_sec)を1.8→2.0-2.2sに延長、または `pitch_thrust_gain` を±0.02から試す |
| 姿勢は安定するが前進ゼロ | replay vs adaptive比較で姿勢補正が推進を殺していないか確認 → 後膝キック振幅強化・キック区間集中度を上げる |
| 適応 < 素再生(悪化) | feedback_gain/attitude_kpを下げる方向。学習トリムの符号を8/11ログと照合 |
| 追従は良いのに滑って進まない | ハード対処を私に提案(足裏ゴム、支持具高さ)。ソフトではデューティ・接地時間配分 |

- パラメータ変更はPi `.env` 優先(コード変更よりロールバック容易)。全設定はmanifestの`config_snapshot`に自動記録される
- ILC有効化(`ILC_GAIN=0.3`から)は「素再生の周期再現性が確認できた後」。events.jsonlの`adaptive_walk_ilc`でaccepted/cycle_rmsを監視

## 安全ルール(絶対)

1. **アクチュエータを動かすAPI(`/target`, `/forward`, `/csv/playback`, `/home`)をあなたから直接叩かない。** 歩行はユーザーのGUI長押しのみ。characterizeも実行前に必ず許可を取る
2. `safety_confirmed` の自動化・自動停止後の再押下要求の緩和・`max_tilt_deg`の引き上げをしない
3. `max_target_rate`等の安全系パラメータは一度に大きく変えない(+20%刻み)
4. Pi側の `config/imu_calibration.json` / `.env` / `Logs/` を上書きしない(pi_deploy.pyはgit追跡ファイル+distのみ同期なので通常は安全)
5. 各イテレーションで小さくgit commit(実験IDをメッセージに含める)
6. 作業ツリーに `imu_bmx055.py` + `tests/test_imu_bmx055.py` の未コミット変更(BMM150電源ON後3ms待ち)がある — 別作業なので**触らず残す**

## 終了条件

- 成功: 支持具なし3周期以上の前進歩行 → 該当ランのID・metrics・設定を記録してdocsに1ページ残す
- 各セッション終了時: 未コミットを整理し、Pi側 `.env` の最終状態を私に報告
