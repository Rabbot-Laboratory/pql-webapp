# Raspberry Pi セットアップ手順

このドキュメントは、新しい Raspberry Pi に `Aircompressor_Robot` を転送し、
実機運用できるところまで持っていくための手順書です。

対象:
- Raspberry Pi 上で `highend_server` を本番運用したい
- ESP32 Front / Back を USB 接続して使いたい
- Web UI を `http://<pi-ip>:8000/` で見たい

この手順は、これまでの実運用で詰まりやすかったポイントも含めて整理しています。

## 1. 前提

想定構成:
- Raspberry Pi OS
- Python 3.11 前後
- ESP32 が 2 台
  - Front
  - Back
- USB-UART bridge は `CP2102N` 系を想定

推奨:
- 実機運用は Raspberry Pi
- Windows は検証用
- Front / Back の識別は Linux の `udev` で固定名化

## 2. Raspberry Pi 側の初期準備

まず OS を更新し、必要な最低限のパッケージを入れます。

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y python3 python3-venv python3-pip git
```

必要に応じて Wi-Fi もつないでおきます。

疎通確認:

```bash
ping -c 3 1.1.1.1
ping -c 3 pypi.org
```

## 3. 配置先ディレクトリを作る

実運用のロボット(rabbot-labo)は `~/pql-webapp` を使っています。以下その前提で書きます
(パス・ユーザー名は環境に合わせて読み替え)。

```bash
git clone https://github.com/Rabbot-Laboratory/pql-webapp.git ~/pql-webapp
```

## 4. PC からコードを転送する

初回は上記の `git clone` が最簡です。以後の更新は、開発PCから差分同期スクリプトを使うのが標準です:

```powershell
# 開発PC側(要 pip install -e ".[deploy]" と config/pi_connection.json — 雛形は
# config/pi_connection.example.json)
python scripts/pi_deploy.py --dry-run    # 転送内容の確認
python scripts/pi_deploy.py --restart    # md5差分同期 + サービス再起動 + ヘルス確認
```

`pi_deploy.py` はgit追跡ファイルと `web-vue/dist` のみ同期し、Pi側の
`config/imu_calibration.json` / `.env` / `Logs/` には触りません。実験ログの回収は
`python scripts/pi_fetch_logs.py`(最新ラン)です。

手動 `scp -r` を使う場合は `.venv` / `node_modules` / `__pycache__` /
`.pytest_cache` / `Logs` を除外してください。

## 5. Frontend の扱い

本番運用では、FastAPI が `web-vue/dist` をそのまま配信します。

そのため、PC 側で先に build してから転送するのが楽です。

Windows 側:

```powershell
cd web-vue
npm.cmd install
npx vite build
```

これで生成された `web-vue/dist` は `pi_deploy.py` が一緒に同期します。

補足:
- `npx vite build` は型チェックを行わない。フル型チェックは
  `npx vue-tsc -p tsconfig.app.json --noEmit`(three.js型定義欠如などの既存エラーあり。
  新規エラーが増えていないかだけ確認する)
- Raspberry Pi 上でビルドしてもよいが、本番は `dist` があれば十分

## 6. Raspberry Pi 上で Python 環境を作る

```bash
cd ~/pql-webapp
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev,pi-sensors]"   # pi-sensors = smbus2/spidev/evdev(IMU・ADC・ゲームパッド)
```

I2C / SPI を有効化して再起動:

```bash
sudo raspi-config    # Interface Options → I2C: Enable, SPI: Enable
sudo reboot
```

確認:

```bash
python -c "import fastapi, uvicorn, serial, smbus2, spidev; print('ok')"
ls /dev/i2c-1 /dev/spidev0.*
i2cdetect -y 1       # BMX055: 0x18(加速度) 0x68(ジャイロ) 0x10(磁気)
```

接地センサADC(任意): 5V駆動のMCP3208をSPI0 CE0に接続(PiとはTXU0304レベルシフタ経由、
`/dev/spidev0.0`、CH0-3=右前/左前/右後/左後)。配線詳細は readme の
「Raspberry Pi IMU / ADC sensors」を参照。接地判定はサーバー側で行われ、既定では制御に
使われない(表示・記録のみ)。

## 7. まずは demo モードで起動確認

実機を触る前に、まずサーバー自体が立つことを確認します。

```bash
cd ~/Desktop/Aircompressor_Robot
. .venv/bin/activate
python -m highend_server --demo
```

別端末から:

```bash
curl http://127.0.0.1:8000/api/health
```

期待:
- `ok: true`
- `emulate_devices: true`

ブラウザ:

```text
http://<pi-ip>:8000/
```

### 7.1 IMU センサ・スタビライゼーションを使う場合(任意)

BMX055 IMU による姿勢表示・スタビライゼーション(胴体水平維持)機能を使う場合は、
`HIGHEND_SENSORS_ENABLED=true` に加えて、必要に応じて以下の環境変数で調整します
(すべて省略可、既定値のままで動作します)。

```bash
# IMU フュージョン(専用 100Hz スレッド)
HIGHEND_IMU_SAMPLE_RATE_HZ=100          # 加速度+ジャイロ読み取りレート
HIGHEND_IMU_MAG_SAMPLE_RATE_HZ=20       # 磁気センサ読み取りレート
HIGHEND_MAHONY_KP=0.8
HIGHEND_MAHONY_KI=0.02

# 姿勢スタビライゼーション(デフォルトは常に無効、API から明示的に有効化するまで動かない)
HIGHEND_STABILIZATION_RATE_HZ=25
HIGHEND_STABILIZATION_MAX_CORRECTION=120
HIGHEND_STABILIZATION_MAX_CORRECTION_RATE=400
HIGHEND_STABILIZATION_MAX_TILT_DEG=30
HIGHEND_STABILIZATION_MAX_STALENESS_SEC=0.2
```

環境変数は作業ディレクトリの **`.env` ファイル**に書くのが標準です(gitignore済み、
systemdの `WorkingDirectory` が設定されていれば自動で読まれる)。適応歩行の調整ノブ
(`HIGHEND_ADAPTIVE_WALK_*`、ILC・接地ゲート等の全24項目)は `src/highend_server/config.py`
が正で、運用手順は `docs/adaptive_walking_hardware_guide.md` と
`docs/walking_fix_session_checklist.md` を参照してください。

較正手順(水平 → ジャイロゼロ → 磁気較正)、GUI 操作、REST API、そして
**実機での軸符号確認チェックリスト(初回電源投入時に必須)** は
`docs/imu_stabilization_guide.md` にまとめてあります。実機でスタビライゼーションを
有効化する前に、必ずこのチェックリストを一読してください。

## 8. USB シリアルの確認

ESP32 を 2 台挿したら、まず Linux からどう見えているか確認します。

```bash
ls -l /dev/ttyUSB*
ls -l /dev/serial/by-id
```

さらに属性を見る:

```bash
udevadm info -a -n /dev/ttyUSB0 | less
udevadm info -a -n /dev/ttyUSB1 | less
udevadm info --query=all --name=/dev/ttyUSB0
udevadm info --query=all --name=/dev/ttyUSB1
```

確認したいのは主に:
- `ID_SERIAL_SHORT`
- `ID_VENDOR_ID`
- `ID_MODEL_ID`
- `ID_MODEL`

## 9. Front / Back を固定名にする

本番では `ttyUSB0 / ttyUSB1` に依存しない方が安全です。

このプロジェクトはデフォルトで次の名前を期待します。

- `/dev/ttyUSB-Front`
- `/dev/ttyUSB-Back`

### 9.1 まず serial を確定する

片方ずつ挿して確認するのが確実です。

例:
- Front の `ID_SERIAL_SHORT = 021d30458b50ed119d061cf1ccf2b06c`
- Back の `ID_SERIAL_SHORT = 00fc8cb31928ee119f6e0dd8f49e3369`

### 9.2 udev ルールを書く

```bash
sudo nano /etc/udev/rules.d/99-esp32-device.rules
```

例:

```udev
SUBSYSTEM=="tty", ENV{ID_SERIAL_SHORT}=="021d30458b50ed119d061cf1ccf2b06c", SYMLINK+="ttyUSB-Front"
SUBSYSTEM=="tty", ENV{ID_SERIAL_SHORT}=="00fc8cb31928ee119f6e0dd8f49e3369", SYMLINK+="ttyUSB-Back"
```

反映:

```bash
sudo udevadm control --reload-rules
sudo udevadm trigger
```

確認:

```bash
ls -l /dev/ttyUSB-Front /dev/ttyUSB-Back
```

### 9.3 補足

今回のように `CP2102N USB to UART Bridge Controller` で見えている場合、
通常は **ESP32 にファームを書き込んでも serial は変わりません**。

変わることが多いのは:
- `/dev/ttyUSB0` / `/dev/ttyUSB1`
- USB の location

なので、`ID_SERIAL_SHORT` ベースの `udev` ルールが安定です。

## 10. 実機モードで手動起動

```bash
cd ~/Desktop/Aircompressor_Robot
. .venv/bin/activate
python -m highend_server
```

確認:

```bash
curl http://127.0.0.1:8000/api/health
```

期待:
- `ok: true`
- `emulate_devices: false`
- `connection_state: connected`

まとめて点検するには専用のプリフライトを使います(推奨。以後、実機セッションの最初に毎回実行):

```bash
cd ~/pql-webapp && .venv/bin/python scripts/preflight.py
```

Python/git/I2C/SPI/BMX055/シリアル2系統/APIヘルス/ログ書き込み/ディスク残量を一括確認します。

## 11. systemd で自動起動

本番では `systemd` を推奨します。

```bash
sudo nano /etc/systemd/system/highend-control.service
```

例:

```ini
[Unit]
Description=Highend Control Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=rabbot
WorkingDirectory=/home/rabbot/pql-webapp
ExecStart=/home/rabbot/pql-webapp/.venv/bin/python -m highend_server
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1
Environment=HIGHEND_SENSORS_ENABLED=true

[Install]
WantedBy=multi-user.target
```

`WorkingDirectory` が設定されているため、リポジトリ直下の `.env` に書いた
`HIGHEND_*` 変数もサーバー起動時に読み込まれます(歩行パラメータの調整はサービス
ファイルではなく `.env` 側で行い、`sudo systemctl restart highend-control.service`
で反映するのが運用の基本)。

反映:

```bash
sudo systemctl daemon-reload
sudo systemctl enable highend-control.service
sudo systemctl start highend-control.service
```

確認:

```bash
systemctl status highend-control.service
journalctl -u highend-control.service -n 100 --no-pager
```

## 12. よく使うコマンド

サービス停止:

```bash
sudo systemctl stop highend-control.service
```

再起動:

```bash
sudo systemctl restart highend-control.service
```

無効化:

```bash
sudo systemctl disable highend-control.service
```

ヘルス確認:

```bash
curl http://127.0.0.1:8000/api/health
```

## 13. よくある詰まりどころ

### 13.1 `/dev/ttyUSB-Front` が無い

原因候補:
- `udev` ルールの serial が古い
- Front 側 ESP が未接続
- USB ケーブル不良

確認:

```bash
ls -l /dev/serial/by-id
udevadm info --query=all --name=/dev/ttyUSB0
udevadm info --query=all --name=/dev/ttyUSB1
```

### 13.2 service が落ちる

ログ確認:

```bash
journalctl -u highend-control.service -n 100 --no-pager
```

よくある原因:
- Front / Back の symlink が無い
- USB が一瞬不安定
- 別プロセスがポートを掴んでいる

### 13.3 旧サーバーや別スクリプトと競合する

特に `main5.py` や旧サーバーが残っていると、
- シリアル競合
- UDP `6060` 競合

が起きます。

確認:

```bash
sudo ss -lunp | grep 6060
sudo fuser -v /dev/ttyUSB0 /dev/ttyUSB1
```

### 13.4 Raspberry Pi に外向きネットワークが無い

依存インストールで失敗する場合は:

```bash
ping -c 3 1.1.1.1
ping -c 3 pypi.org
cat /etc/resolv.conf
```

を確認します。

## 14. 実運用のおすすめ

- 本番: Raspberry Pi + `udev` + `systemd`
- 検証: Windows ランチャー
- Frontend は PC 側で `npm run build` してから転送
- `web-vue/dist` があれば Pi 側で Node を必須にしない運用が可能

## 15. 最終確認チェックリスト

- `python -m highend_server --demo` で GUI が開く
- `/dev/ttyUSB-Front` と `/dev/ttyUSB-Back` が存在する
- `python -m highend_server` で `connection_state: connected`
- `http://<pi-ip>:8000/` で GUI が開く
- `highend-control.service` が `active`

---

必要なら次に、この手順書に合わせて
- `.service` ファイルの雛形を repo 内へ追加
- `udev` ルールの雛形も repo に置く

ところまで整えられます。
