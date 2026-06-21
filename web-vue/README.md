# Air Compressor Robot

This repository currently contains two generations of the control stack.

- Legacy Raspberry Pi control module: `Highend_Ctrl_mod.py`
- Legacy native GUI: `Native_GuiApp_main2.py`
- New browser-first server scaffold: `src/highend_server/`
- Production-oriented Vue UI scaffold: `web-vue/`

## New server scaffold

Install the new server in editable mode:

```bash
python -m pip install -e .[dev]
```

Run the API server:

```bash
python -m highend_server
```

Run the demo server without hardware:

```bash
python -m highend_server --demo
```

## Runtime flows

- Raspberry Pi production flow:
  - Keep using `python -m highend_server`
  - The server expects Linux-side stable names such as `/dev/ttyUSB-Front` and `/dev/ttyUSB-Back`
  - `systemd` auto-start remains the recommended production setup

- Windows validation flow:
  - Use the Windows launcher instead of calling `python -m highend_server` directly
  - The launcher resolves Front/Back from saved COM fingerprints and then sets
    `HIGHEND_FRONT_PORT_NAME` / `HIGHEND_BACK_PORT_NAME` automatically

First-time Windows setup:

```powershell
python scripts/detect_windows_ports.py list
python scripts/detect_windows_ports.py bind --front COM3 --back COM4
```

After that, launch from Windows with:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_windows.ps1
```

Or run the Windows launcher directly:

```powershell
python scripts/detect_windows_ports.py launch
```

Windows demo launch:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_windows.ps1 -Demo
```

Development mode without hardware:

```bash
HIGHEND_EMULATE_DEVICES=true python -m highend_server
```

PowerShell example:

```powershell
$env:HIGHEND_EMULATE_DEVICES = "true"
python -m highend_server
```

PowerShell demo shortcut:

```powershell
python -m highend_server --demo
```

Then open the browser dashboard:

```text
http://<raspberry-pi-host>:8000/
```

## Vue UI scaffold

The staged production UI now lives in `web-vue/`.

Install frontend dependencies:

```bash
cd web-vue
npm install
```

Run the Vite development server:

```bash
npm run dev
```

When `web-vue/dist` exists, the FastAPI app serves it automatically instead of
the legacy `web/` directory.

Main endpoints:

- `GET /api/health`
- `GET /api/actuators`
- `GET /api/preview/legs`
- `POST /api/actuators/{id}/target`
- `POST /api/actuators/{id}/gain`
- `POST /api/actuators/{id}/gain/request`
- `POST /api/actuators/{id}/capture`
- `POST /api/motions/fixed`
- `POST /api/csv/playback/start`
- `POST /api/csv/playback/stop`
- `WS /api/ws`

## Directory layout

```text
src/highend_server/
  api/           FastAPI routes and WebSocket delivery
  application/   Robot control use cases
  domain/        Shared application models
  protocol/      64-bit ESP32 frame encoding and decoding
  transport/     Serial gateway boundary
tests/           Protocol-focused unit tests
web/             Future browser client
```

## Migration intent

The new structure separates:

- `protocol`: 64-bit ESP32 frame encoding and decoding
- `transport`: serial gateway boundary
- `application`: robot control use cases
- `api`: REST and WebSocket delivery to browser clients

The current `StubSerialGateway` is intentional. It lets the API and future web
UI be built before wiring in the real Raspberry Pi serial transport.

When `HIGHEND_EMULATE_DEVICES` is not set, the server now uses the real
`PySerialGateway` and tries to open:

- `HIGHEND_FRONT_PORT_NAME` (default: `/dev/ttyUSB-Front`)
- `HIGHEND_BACK_PORT_NAME` (default: `/dev/ttyUSB-Back`)

When `HIGHEND_EMULATE_DEVICES=true`, the stub gateway acts like a dummy ESP32
pair:

- periodic fake sensor telemetry is streamed over WebSocket
- target commands update the dummy actuator values
- gain requests return fake gain and capture values
- the browser dashboard renders a focused leg preview from the emulated joint state
