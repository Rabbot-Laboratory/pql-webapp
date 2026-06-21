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

### Raspberry Pi IMU / ADC sensors

The optional walking-feedback sensors are disabled by default so that Windows
and demo launches keep working without Pi-only packages.

Install the sensor extras on Raspberry Pi:

```bash
python -m pip install -e .[pi-sensors]
```

Enable I2C and SPI on the Pi:

```bash
sudo raspi-config
```

Expected devices after reboot:

```bash
ls /dev/i2c-1
ls /dev/spidev0.*
i2cdetect -y 1
```

For the CJMCU-055 / BMX055 wiring with `SDO1=GND` and `SDO2=GND`, the default
I2C addresses are:

- Accelerometer: `0x18`
- Gyroscope: `0x68`
- Magnetometer: `0x10`

For two MCP3204 ADCs on SPI0 CE0 and CE1, the default SPI devices are:

- `/dev/spidev0.0`
- `/dev/spidev0.1`

Enable sensor polling:

```bash
HIGHEND_SENSORS_ENABLED=true python -m highend_server
```

With `systemd`, set the same environment variable in `highend-control.service`.
The sensor state is exposed through:

- `GET /api/sensors`
- WebSocket `sensor_state` events

IMU calibration endpoints:

- `POST /api/sensors/imu/calibration/level`
- `POST /api/sensors/imu/calibration/gyro-zero`
- `POST /api/sensors/imu/calibration/reset`

The calibration file is saved as `config/imu_calibration.json`.

Recommended calibration flow:

1. Put the robot in its neutral standing posture.
2. Press "current posture as level" to zero Roll / Pitch.
3. Keep the robot completely still.
4. Press "gyro zero" to average and save gyro drift.
5. Check that corrected Roll / Pitch and Gyro values stay near zero.

Useful environment overrides:

- `HIGHEND_SENSOR_POLL_INTERVAL_SEC=0.05`
- `HIGHEND_SENSOR_I2C_BUS=1`
- `HIGHEND_BMX055_ACCEL_ADDRESS=24`
- `HIGHEND_BMX055_GYRO_ADDRESS=104`
- `HIGHEND_BMX055_MAG_ADDRESS=16`
- `HIGHEND_ADC_SPI_BUS=0`
- `HIGHEND_ADC_SPI_DEVICES=0,1`
- `HIGHEND_ADC_VREF=3.3`

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
- `GET /api/sensors`
- `POST /api/sensors/imu/calibration/level`
- `POST /api/sensors/imu/calibration/gyro-zero`
- `POST /api/sensors/imu/calibration/reset`
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
