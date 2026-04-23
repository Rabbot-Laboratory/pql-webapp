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

ここでは `Desktop` 配下へ配置する前提です。

```bash
mkdir -p ~/Desktop/Aircompressor_Robot
```

## 4. PC からコードを転送する

Windows PowerShell から `scp` で転送する例です。

```powershell
scp -r C:\Users\MaedaNatsuki\Documents\Aircompressor_Robot\* wandora@<pi-ip>:/home/wandora/Desktop/Aircompressor_Robot/
```

注意:
- `.venv`
- `node_modules`
- `__pycache__`
- `.pytest_cache`

のような生成物は基本的に転送不要です。

## 5. Frontend の扱い

本番運用では、FastAPI が `web-vue/dist` をそのまま配信します。

そのため、PC 側で先に build してから転送するのが楽です。

Windows 側:

```powershell
cd C:\Users\MaedaNatsuki\Documents\Aircompressor_Robot\web-vue
npm.cmd install
npm.cmd run build
```

これで生成された `web-vue/dist` を、プロジェクト転送時に一緒に載せます。

補足:
- Raspberry Pi 上で `npm install` / `npm run build` してもよい
- ただし本番は `dist` があれば十分

## 6. Raspberry Pi 上で Python 環境を作る

```bash
cd ~/Desktop/Aircompressor_Robot
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -U pip
python -m pip install -e .[dev]
```

確認:

```bash
python -c "import fastapi, uvicorn, serial; print('ok')"
```

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
User=wandora
WorkingDirectory=/home/wandora/Desktop/Aircompressor_Robot
ExecStart=/home/wandora/Desktop/Aircompressor_Robot/.venv/bin/python -m highend_server
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

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
