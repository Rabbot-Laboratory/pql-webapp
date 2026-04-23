# pql-webapp

空圧駆動4脚ロボット（PQL-A00）のRaspberry Pi側制御サーバーおよびWebダッシュボードです。

- **バックエンド**: FastAPI（Python） — ESP32とシリアル通信し、WebSocket経由でブラウザへテレメトリを配信
- **フロントエンド**: Vue 3 + TypeScript（`web-vue/`）

## ディレクトリ構成

```
src/highend_server/
  api/           REST APIルートおよびWebSocket配信
  application/   ロボット制御ユースケース
  domain/        共通データモデル
  protocol/      ESP32との64bitフレームエンコード/デコード
  transport/     シリアル通信ゲートウェイ
web-vue/         Vue 3フロントエンド
motion/
  fixed/         固定モーションCSV（trot, crawl, pace, bound）
  custom/        ユーザー定義モーション（実行時に生成、Git管理外）
pql-a00_description/    PQL-A00 ROSロボット記述（URDF/メッシュ）
pql-lg00-a1_description/ PQL-LG00-A1 ROSロボット記述（URDF/メッシュ）
tests/           バックエンド単体テスト
scripts/         開発・メンテナンス用スクリプト
docs/            設計ドキュメント
```

## セットアップ

### バックエンド

依存関係をインストール（開発用）:

```bash
pip install -e .[dev]
```

### フロントエンド

```bash
cd web-vue
npm install
```

## 起動方法

### Raspberry Pi（本番）

```bash
python -m highend_server
```

シリアルポートはデフォルトで以下を使用します:

- `/dev/ttyUSB-Front`（前脚側ESP32）
- `/dev/ttyUSB-Back`（後脚側ESP32）

`systemd` による自動起動を推奨します。

### Windows（開発・検証用）

初回のみ、COMポートとESP32の対応を登録:

```powershell
python scripts/detect_windows_ports.py list
python scripts/detect_windows_ports.py bind --front COM3 --back COM4
```

以降の起動:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_windows.ps1
```

### ハードウェアなしでの動作確認（エミュレーションモード）

```bash
python -m highend_server --demo
```

または環境変数で指定:

```bash
HIGHEND_EMULATE_DEVICES=true python -m highend_server
```

PowerShellの場合:

```powershell
$env:HIGHEND_EMULATE_DEVICES = "true"
python -m highend_server
```

エミュレーションモードでは、ダミーのESP32として動作します:

- 偽のセンサーテレメトリをWebSocket経由で配信
- アクチュエータへのコマンドをダミー値で処理
- ブラウザダッシュボードで脚の姿勢プレビューを表示

### フロントエンド開発サーバー

```bash
cd web-vue
npm run dev
```

Viteの開発サーバーが起動し、APIリクエストは自動的に `http://127.0.0.1:8000` へプロキシされます。

ブラウザでアクセス:

```
http://localhost:5173
```

本番ビルド（`web-vue/dist/`）が存在する場合、FastAPIが自動的に静的ファイルとして配信します:

```
http://<raspberry-pi-host>:8000/
```

## 主なAPIエンドポイント

| メソッド | パス | 説明 |
|---|---|---|
| `GET` | `/api/health` | サーバー死活確認 |
| `GET` | `/api/actuators` | 全アクチュエータ状態取得 |
| `GET` | `/api/preview/legs` | 脚プレビュー取得 |
| `POST` | `/api/actuators/{id}/target` | 目標角度送信 |
| `POST` | `/api/actuators/{id}/gain` | PIDゲイン設定 |
| `POST` | `/api/motions/fixed` | 固定モーション再生 |
| `GET` | `/api/motions/library` | モーションライブラリ一覧 |
| `POST` | `/api/csv/playback/start` | CSVモーション再生開始 |
| `POST` | `/api/csv/playback/stop` | CSVモーション再生停止 |
| `WS` | `/api/ws` | テレメトリWebSocket |

## テスト

```bash
pytest
```
