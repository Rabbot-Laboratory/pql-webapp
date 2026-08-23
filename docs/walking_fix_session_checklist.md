# 次回実機セッション チェックリスト（歩行修正 2026-08-23 実装分）

前提: `experiment/2026-07-11` ブランチ(c3c26cc〜、`scripts/pi_deploy.py --restart` で同期)をPiへデプロイ済み。
安全: 手元の物理エア遮断バルブを常時手の届く位置に（自動停止は「最終姿勢保持」で減圧しない）。

## 0. デプロイ確認（5分）
- [ ] `python scripts/preflight.py` 全OK
- [ ] GUI Motionタブに「制御モード / サイクル数」セレクタとゲイトダイアグラム・実験記録カードが出ている

## 1. 接地センサ校正（10分・任意だが推奨）
- [ ] Contactタブの校正ウィザードで4脚それぞれ「無荷重を記録」（脚を浮かせる）→「荷重を記録」（接地して体重をかける）→「校正を保存」
- [ ] 各脚を手で押してSUPPORT表示が正しく反応することを確認（ダメなら以降は接地なしで進めてOK — 制御は既定で接地不使用）

## 2. 軸特性同定（20分）
各軸で（ホーム姿勢から、脚は浮かせた状態推奨）:
```
python scripts/robotctl.py characterize --axis 0 --amplitude 300
```
- [ ] axis 0〜7 実行 → `config/axis_characterization.json` 生成
- [ ] 表示された dead time / t63 / 速度の伸縮非対称をメモ（→ CSV軸別先行の再調整と `config/pneumatic_sim.json` 同定の入力）

## 2.5 立位保持（歩行の前提。フロー: 起動→較正→立つ→歩行）
- [ ] Motionタブ最上部「安全に立つ」: 安全確認チェック→「立ち上がる」
- [ ] Home姿勢へ上昇→保持→**立位OK**バッジ（全軸誤差≤200・傾斜≤5°が2秒継続）を確認
- [ ] 軸別誤差チップを観察: 赤=オーバードライブ補正中（不感帯突破動作）。特定軸が赤のまま張り付くならその軸のゲイン/機構を疑う
- [ ] 立位OKが出ない場合: `.env` の `HIGHEND_STANDING_HOLD_TOLERANCE` / `HIGHEND_STANDING_OVERDRIVE_GAIN` を調整（検証だけしたい時は `HIGHEND_ADAPTIVE_WALK_REQUIRE_STANDING=0` で歩行ゲート解除）
- 歩行ボタンは**立位OKの間だけ**有効。歩行開始で立位保持は自動解除（ハンドオーバー）

## 3. ベースライン計測 — v2 CSVの素の実力（未取得のまま）
- [ ] Motionタブ: モード=**素再生**、サイクル=**1周期** → 長押し（自動停止・自動記録）
- [ ] `python scripts/walk_metrics.py metrics Logs/experiments/<ID>` — 軸別遅れ・飽和率・後脚キック同期ずれを確認
- [ ] 同条件で **3周期** も1本

## 4. 適応モード比較
- [ ] モード=**適応**、3周期 → 記録
- [ ] `walk_metrics.py compare <replayのID> <adaptiveのID>` — 位相進み学習が遅れを縮めたか、飽和/速度制限率が下がったかを確認
- [ ] GUIの軸別バーが上限(200ms)に張り付く軸 = CSV先行不足の軸

## 5. 接地ゲート有効化（1が成功している場合のみ）
`.env` に:
```
HIGHEND_ADAPTIVE_WALK_USE_CONTACT=1
HIGHEND_ADAPTIVE_WALK_KICK_GATE_PHASE=<キック開始位相 0..1>
```
キック開始位相は rabbit_bound.csv の後脚が蹴り始めるフレーム/45。
- [ ] 適応3周期 → compare で**後脚キック同期ずれ**と Pitch RMS の改善を確認
- [ ] ゲイトダイアグラムで「ゲート待ち帯」が毎周期入るならタイムアウト/位相を調整

## 6. ILC（5の接地順序が正しくなってから）
```
HIGHEND_ADAPTIVE_WALK_ILC_GAIN=0.3   # 0=無効
```
- [ ] 適応で5周期以上 → events.jsonl の `adaptive_walk_ilc` で accepted/cycle_rms の推移を確認（悪化時は自動巻き戻し）

## 7. 前進量の記録（毎ラン）
- [ ] 床にテープでマーカー（10cm間隔）、固定スマホで録画
- [ ] 録画開始時に画面ライトを点滅させ、同時に実験ノート（GUIの実験記録カード）に「LED flash」と記入 → 事後同期
- [ ] ラン毎に「1周期あたり前進量」を目視計測してノートへ

## トラブル時
- 自動停止後にボタンを押し続けても再開しない仕様（一度離して押し直す）
- 接地データ欠測時はゲート・支持脚補正は自動で無効化（センサ不調でも歩行は従来どおり）
- 全設定は `manifest.json` の config_snapshot に自動記録される — 「良かった設定」はそこから復元
