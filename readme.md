# Air Compressor Robot

Browser-first control stack for the pneumatic quadruped robot.

- FastAPI control server: `src/highend_server/`
- Production Vue UI: `web-vue/`

## Server

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

The installation sensors use one 5 V MCP3208 on SPI0 CE0. A TXU0304 level
shifter translates the Raspberry Pi's 3.3 V SPI logic to the ADC's 5 V logic:

- `/dev/spidev0.0` (channels 0 through 7)

Enable sensor polling:

```bash
HIGHEND_SENSORS_ENABLED=true python -m highend_server
```

With `systemd`, set the same environment variable in `highend-control.service`.
The sensor state is exposed through:

- `GET /api/sensors`
- WebSocket `sensor_state` events

Required hardware is also summarized without making server startup depend on
device presence:

- `GET /api/hardware`
- WebSocket `hardware_status` events
- `GET /api/health` keeps `ok=true` while the API is running and reports
  physical readiness separately as `robot_ready`

The WebUI shows a dismissible warning for missing Front/Back ESP32, BMX055, or
MCP3208 devices. Closing it leaves a small warning chip, and serial ports keep
retrying in the background. The F710 is optional and does not create a warning.

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
- `HIGHEND_ADC_SPI_DEVICES=0`
- `HIGHEND_ADC_VREF=5.0`

Then open the browser dashboard:

```text
http://<raspberry-pi-host>:8000/
```

### Logitech F710 input observation

The UI supports both controller paths through one `GamepadState` model:

- F710 USB receiver connected to the Raspberry Pi (`evdev`)
- F710 connected to the browser PC (browser Gamepad API over WebSocket)

Both paths are **observation and experiment logging only**. They are not wired
to actuator commands. The provisional deadman input is LB and must be verified
on the physical controller before any future command mapping is enabled.

For the recommended Pi-local path, install the `pi-sensors` extras and start the
server with local gamepad input explicitly enabled:

```bash
python -m pip install -e '.[pi-sensors]'
HIGHEND_GAMEPAD_LOCAL_ENABLED=true python -m highend_server
```

Useful checks and overrides:

```bash
ls -l /dev/input/event*
python -c "from evdev import InputDevice, list_devices; print([(p, InputDevice(p).name) for p in list_devices()])"

# Optional if automatic F710 name matching is not sufficient:
HIGHEND_GAMEPAD_DEVICE_PATH=/dev/input/event3
HIGHEND_GAMEPAD_NAME_MATCH=F710
```

Open the **Controller** tab to inspect normalized axes, buttons, source,
staleness, and deadman state. For the PC path, click **Enable browser input**
and then press a controller button so the browser exposes the device.

### IMU attitude stabilization

On top of the display-only IMU pipeline above, the server can optionally close
a feedback loop that uses the fused Roll/Pitch to keep the body level, and to
protect CSV motion playback against attitude drift. It is **disabled by
default** and never auto-enables on boot.

- Feedback control: Roll/Pitch error -> per-axis PID -> leg mixing matrix ->
  per-actuator position corrections, added on top of the user/CSV base target.
  Yaw is display-only and never used for control.
- Safety mechanisms: correction clamp and rate limiter, auto-disable on
  excessive tilt (>30 deg default), stale attitude (>0.2s default), repeated
  serial send failures, or an internal loop error, with a smooth ramp-to-zero
  on any disable (manual or automatic).
- Control via `GET`/`POST /api/control/stabilization` or the "Sensors &
  Control" tab in the Vue UI.
- IMU calibration also includes a magnetometer (hard/soft-iron) flow:
  `POST /api/sensors/imu/calibration/mag/start` / `mag/finish` / `mag/cancel`.
  Most calibration endpoints (level, gyro-zero, reset, mag/start, mag/finish)
  return `409` while stabilization is enabled/active — disable it first.

See `docs/imu_stabilization_guide.md` (Japanese) for the full operator guide,
including the on-robot axis-sign verification checklist that must be run once
before trusting the corrections on real hardware.

## MuJoCo walking simulation

The CAD-derived quadruped can be run with per-cylinder pneumatic lag, asymmetric
extension/retraction speeds, bounded online adaptive phase compensation, and
IMU-driven adaptive body-level gait trim. Foot-contact sensing is not required.

```powershell
python -m pip install -e ".[simulation,dev]"
python scripts\run_simulation.py --duration 12
```

For a repeatable headless run with telemetry:

```powershell
python scripts\run_simulation.py --headless --duration 12 --log Logs\simulation\crawl.csv
```

Disable only the IMU feedback for an A/B comparison with the same gait:

```powershell
python scripts\run_simulation.py --headless --duration 12 --no-imu-control
```

See `docs/walking_simulation_guide.md` for model assumptions, parameter
identification, comparison without adaptation, and the hardware transfer flow.

## Hardware IMU-adaptive walking

The simulation control law is also available as an isolated real-hardware
target generator. The Motion page has a press-and-hold **Forward** button that
runs the existing crawl motion at 20% amplitude while learning per-cylinder
phase lead and applying bounded IMU Roll/Pitch trim. It does not use contact
sensors.

The server requires fresh IMU data and all eight actuator telemetry channels,
keeps this mode exclusive from manual/CSV/stabilization control, and stops
target updates on button/network lease loss, stale IMU, excessive tilt, or
serial failure. Stopping holds the last commanded posture; a physical air
cutoff remains mandatory. See `docs/adaptive_walking_hardware_guide.md` for the
supported low-pressure first-run procedure and tuning parameters.

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

Build `web-vue/dist` (`npm run build`) and the FastAPI app serves it
automatically at `/`.

Main endpoints:

- `GET /api/health`
- `GET /api/actuators`
- `GET /api/sensors`
- `POST /api/sensors/imu/calibration/level`
- `POST /api/sensors/imu/calibration/gyro-zero`
- `POST /api/sensors/imu/calibration/reset`
- `POST /api/sensors/imu/calibration/mag/start`
- `POST /api/sensors/imu/calibration/mag/finish`
- `POST /api/sensors/imu/calibration/mag/cancel`
- `GET /api/control/stabilization`
- `POST /api/control/stabilization`
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
tests/           Unit and API tests
web-vue/         Production Vue 3 UI
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
