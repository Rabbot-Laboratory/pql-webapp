# Single Leg Control

空圧ロボットの1脚だけを、1台のESP32で確認・調整するための独立アプリです。
本番用の `web-vue/` と `src/highend_server/` は変更せず、このディレクトリだけで動作します。

## この版の範囲

- ESP32は1台だけ（hip = CH 0、knee = CH 1）
- APIとGUIに公開するアクチュエータはhip/kneeの2軸だけ
- 3Dビューは1脚だけを描画し、現在位置と目標位置を重ねて表示
- IMU、MCP3208、接地センサ、ゲームパッド、後側ESP32は初期化も監視もしない
- 未搭載センサが原因のエラー／警告は発生しない
- ESP32プロトコル上の未使用CH 2/3には、安全な固定値を送信

## セットアップ

Python 3.11以降とNode.jsを用意し、このディレクトリで実行します。

```powershell
python -m pip install -e .
cd web
npm install
npm run build
cd ..
```

## 実機で起動

Windows（例: COM3）:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run.ps1 -PortName COM3
```

Raspberry Piでは、安定したデバイス名を `.env` の `SINGLE_LEG_PORT_NAME` に設定します。

```bash
cp .env.example .env
# .env を編集: SINGLE_LEG_PORT_NAME=/dev/ttyUSB-Leg
python -m single_leg_server
```

ブラウザで `http://localhost:8100/`（別PCからはPiのIPアドレス）を開きます。

## ハードウェアなしで確認

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run.ps1 -Demo
```

デモモードでも1台のESP32・2軸だけが再現されます。外部センサのダミー状態は作りません。

## 開発時の画面起動

サーバーを8100番で起動したまま、別ターミナルで実行します。

```powershell
cd web
npm run dev
```

開発画面は `http://localhost:5174/` です。

## 環境変数

| 変数 | 既定値 | 用途 |
| --- | --- | --- |
| `SINGLE_LEG_PORT_NAME` | `/dev/ttyUSB-Leg` | 1台だけ使用するESP32のシリアルポート |
| `SINGLE_LEG_SERIAL_BAUDRATE` | `115200` | 通信速度 |
| `SINGLE_LEG_API_PORT` | `8100` | Web/APIポート |
| `SINGLE_LEG_EMULATE_DEVICES` | `false` | 1台・2軸のデモモード |
| `SINGLE_LEG_UNUSED_POSITION` | `2048` | プロトコル上の未使用CH 2/3へ送る位置値 |
| `SINGLE_LEG_UNUSED_COMMAND` | `900` | プロトコル上の未使用CH 2/3へ送る指令値 |

ESP32が未接続の間もサーバーとGUIは起動し、画面には「ESP32 待機中」とだけ表示します。
バックグラウンドで再接続し、接続後は自動的に操作可能になります。

