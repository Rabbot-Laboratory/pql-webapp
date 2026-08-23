# Air Compressor Robot (PQL-A00)

Browser-first control stack for the pneumatic quadruped robot.

- FastAPI control server: `src/highend_server/`
- Production Vue UI: `web-vue/`
- MuJoCo digital twin: `src/highend_server/simulation/` + `simulation/`
- Operations / experiment docs: `docs/`

## Quick start (fresh environment)

```bash
git clone https://github.com/Rabbot-Laboratory/pql-webapp.git
cd pql-webapp
python -m pip install -e ".[dev]"          # server + tests
cd web-vue && npm install && npx vite build && cd ..
python -m highend_server --demo            # emulated hardware, no robot needed
# open http://127.0.0.1:8000/
```

Verify the checkout:

```bash
python -m pytest tests -q                  # 260+ tests, no hardware required
python -m ruff check src tests scripts
```

Optional extras:

```bash
python -m pip install -e ".[pi-sensors]"   # Raspberry Pi only: IMU/ADC/gamepad
python -m pip install -e ".[simulation]"   # MuJoCo walking simulation
python -m pip install -e ".[deploy]"       # dev machine: SSH deploy to the Pi
```

For the full Raspberry Pi bring-up (OS, udev serial names, systemd, sensors)
see `docs/raspberry_pi_setup_tutorial.md`.

## Configuration

All settings live in `src/highend_server/config.py` (pydantic-settings). Every
field can be overridden by an environment variable named `HIGHEND_<FIELD>` or
by a `.env` file in the working directory (`.env` is gitignored — it is the
standard way to tune the robot on the Pi; a systemd unit with
`WorkingDirectory=` set picks it up automatically).

Machine-local files (all gitignored, never deployed):

- `config/imu_calibration.json` — IMU level/gyro/mag calibration
- `config/contact_calibration.json` — per-leg foot-contact thresholds
- `config/axis_characterization.json` — measured cylinder step responses
- `config/pi_connection.json` — SSH target for the deploy scripts
  (copy from `config/pi_connection.example.json`)
- `.env` — Pi-side tuning overrides

## Server

```bash
python -m highend_server            # real hardware (serial + sensors)
python -m highend_server --demo     # emulated devices + stationary IMU
python -m highend_server --replay Logs/experiments/<id>/   # replay a recorded
                                    # run through the live Mahony filter
```

- Raspberry Pi production flow: `systemd` unit running `python -m highend_server`
  with stable serial names `/dev/ttyUSB-Front` / `/dev/ttyUSB-Back` (udev rules
  in the Pi tutorial) and `HIGHEND_SENSORS_ENABLED=true`.
- Windows validation flow: use the launcher, which resolves COM ports from
  saved fingerprints:

```powershell
python scripts/detect_windows_ports.py list
python scripts/detect_windows_ports.py bind --front COM3 --back COM4
python scripts/detect_windows_ports.py launch          # or scripts\run_windows.ps1
```

- Development without hardware: `HIGHEND_EMULATE_DEVICES=true python -m highend_server`
  (the stub gateway emulates both ESP32s; `--demo` additionally emulates the IMU/ADC).

## Scripts

| Script | Purpose |
|---|---|
| `scripts/preflight.py` | Pre-run health check (python/git/I2C/SPI/BMX055/serial/API/logs/disk) |
| `scripts/robotctl.py` | Operations CLI: status, sensors, experiment start/stop/note/list, `characterize` (**moves an actuator** — per-axis step-response identification) |
| `scripts/walk_metrics.py` | Analyze a recorded run: per-axis pneumatic lag, saturation %, rate-limit duty, rear-kick sync delta, roll/pitch RMS, contact duty; `compare` for A/B |
| `scripts/pi_deploy.py` | Checksum-based deploy of tracked files + `web-vue/dist` to the Pi (`--dry-run`, `--restart`); needs `.[deploy]` extra and `config/pi_connection.json` |
| `scripts/pi_fetch_logs.py` | Pull experiment run directories from the Pi into `Logs/experiments/pi/` |
| `scripts/analyze_experiment_logs.py` | Batch integrity validation + inventory of experiment logs |
| `scripts/run_simulation.py` | MuJoCo walking simulation (`--headless --duration N --no-imu-control ...`) |
| `scripts/detect_windows_ports.py` | Windows COM-port fingerprinting and launcher |

## Hardware IMU-adaptive walking

The Motion tab has a press-and-hold **Forward** button that plays
`Motion/Fixed Motion/rabbit_bound.csv` (rear-driven bound, 45 frames x 0.04 s,
MuJoCo-derived with baked per-axis phase advance) at full amplitude after a 2 s
smooth ramp, while learning per-cylinder phase lead (frozen while an axis is
saturated or rate-limited) and applying bounded IMU Roll/Pitch trim.

Run options on the walk card:

- **Mode**: `adaptive` (default) or `replay` — replay disables every adaptive
  term for baseline measurement.
- **Cycles**: run exactly 1/3 full-amplitude gait cycles, auto-stop, and
  auto-record an experiment (`walk-<mode>-<N>cyc`).
- After any automatic stop the button must be **released and pressed again**;
  held keepalives are rejected with 409 (prevents unintended restarts).

Safety: fresh IMU + all eight actuator telemetry channels required; exclusive
with manual/CSV/stabilization control; auto-stop on lease loss (450 ms), stale
IMU (0.2 s), tilt > 12 deg, or serial failure. Stopping holds the last posture
— **a physical air cutoff remains mandatory**.

Opt-in layers (all default-off, see `config.py`): foot-contact kick gate and
stance-masked attitude correction (`HIGHEND_ADAPTIVE_WALK_USE_CONTACT`),
per-cycle ILC feed-forward (`HIGHEND_ADAPTIVE_WALK_ILC_GAIN`), pitch-
proportional kick scaling (`HIGHEND_ADAPTIVE_WALK_PITCH_THRUST_GAIN`).

Guides: `docs/adaptive_walking_hardware_guide.md` (operator guide),
`docs/walking_fix_session_checklist.md` (hardware session procedure),
`docs/codex_walking_loop_prompt.md` (iteration-loop playbook + current state).

## Experiment logging

`POST /api/experiments/start` (or `robotctl experiment start`, or the Motion
tab's experiment card) records a per-run directory under
`Logs/experiments/<id>/`:

- `manifest.json` — git SHA, all gains, full config snapshot
- `telemetry.csv` — 25 Hz x 8 actuators, 59 columns (attitude raw/level/control,
  per-axis targets/corrections, walk phase/cycle, per-axis phase lead /
  saturation / rate-limit / ILC, foot contact)
- `events.jsonl` — stabilization/walk/gate/ILC/calibration events
- `notes.md` — operator notes

Cycle-bounded walks record themselves automatically. Analyze with
`python scripts/walk_metrics.py metrics <dir>`.

## Raspberry Pi IMU / ADC sensors

Optional sensors are disabled by default so Windows/demo launches work without
Pi-only packages. On the Pi:

```bash
python -m pip install -e ".[pi-sensors]"
sudo raspi-config           # enable I2C + SPI, then reboot
ls /dev/i2c-1 /dev/spidev0.*
i2cdetect -y 1
HIGHEND_SENSORS_ENABLED=true python -m highend_server
```

- BMX055 (CJMCU-055, `SDO1=GND`/`SDO2=GND`): accel `0x18`, gyro `0x68`, mag `0x10`
- Foot-contact ADC: one 5 V MCP3208 on SPI0 CE0 behind a TXU0304 level shifter
  (`/dev/spidev0.0`, channels 0-3 = FR/FL/RR/RL feet, `HIGHEND_ADC_VREF=5.0`).
  Contact detection runs server-side (hysteresis + debounce, calibrated via
  `GET/PUT /api/sensors/contact-calibration` or the Contact tab wizard) but is
  **not used by control unless `HIGHEND_ADAPTIVE_WALK_USE_CONTACT=true`**.

IMU calibration (Sensors & Control tab, saved to `config/imu_calibration.json`):
level -> gyro-zero -> optional magnetometer flow. Calibration endpoints return
409 while stabilization or walking is active.

## IMU attitude stabilization

Optional Roll/Pitch feedback loop (disabled by default, never auto-enables):
PID -> model-derived leg mixing -> bounded per-actuator corrections on top of
the base target, with clamps, rate limiting, and auto-disable on excessive
tilt / stale IMU / serial failures. Control via
`GET/POST /api/control/stabilization` or the "Sensors & Control" tab.
Mutually exclusive with adaptive walking. Full operator guide (Japanese):
`docs/imu_stabilization_guide.md`.

## MuJoCo walking simulation

```bash
python -m pip install -e ".[simulation,dev]"
python scripts/run_simulation.py --duration 12
python scripts/run_simulation.py --headless --duration 12 --log Logs/simulation/run.csv
python scripts/run_simulation.py --headless --duration 12 --no-imu-control   # A/B
```

See `docs/walking_simulation_guide.md`. Note: the sim's pneumatic parameters
(`config/pneumatic_sim.json`) are placeholders until populated from
`robotctl characterize` measurements.

## Logitech F710 input observation

Both controller paths (Pi-local `evdev` receiver and browser Gamepad API) are
**observation and logging only** — not wired to actuator commands. Enable the
Pi-local path with `HIGHEND_GAMEPAD_LOCAL_ENABLED=true` (needs `pi-sensors`
extras); inspect on the **Controller** tab. Overrides:
`HIGHEND_GAMEPAD_DEVICE_PATH`, `HIGHEND_GAMEPAD_NAME_MATCH`.

## Vue UI

```bash
cd web-vue
npm install
npm run dev        # Vite dev server against a running API
npx vite build     # production bundle; FastAPI serves web-vue/dist at /
```

`npx vite build` does not type-check. The full check is
`npx vue-tsc -p tsconfig.app.json --noEmit`, which currently reports
pre-existing errors (missing `@types/three` etc.) — only watch for new ones.

Tabs: Dashboard, Kinematics, Motion (walk card + gait diagram + experiment
recording + motion library/CSV editor), Contact Sensors, Controller,
Sensors & Control.

## API surface

REST + one WebSocket (`WS /api/ws`, snapshot on connect then typed events).
Key groups (authoritative list: `src/highend_server/api/routes.py`):

- health/status: `GET /api/health`, `/api/hardware`, `/api/actuators`, `/api/sensors`, `/api/gamepad`
- actuators: `POST /api/actuators/{id}/target|gain|gain/request|gain/save|capture`
- motions: `GET/POST/DELETE /api/motions/library*`, `POST /api/motions/fixed`, `POST /api/motions/import/legacy-csv`, `POST /api/csv/playback/start|stop`
- walking: `GET /api/control/adaptive-walk`, `POST /api/control/adaptive-walk/forward` (pressed/safety/cycles/mode), `POST /api/control/home`
- stabilization: `GET/POST /api/control/stabilization`
- sensors: `POST /api/sensors/imu/calibration/*`, `GET/PUT /api/sensors/contact-calibration`
- experiments: `POST /api/experiments/start|stop|note`, `GET /api/experiments`, `GET /api/experiments/latest`
- telemetry recording: `GET/POST /api/telemetry/recording*`

## Directory layout

```text
src/highend_server/
  api/           FastAPI routes and WebSocket delivery
  application/   Control use cases (walking, stabilization, ILC, experiments)
  domain/        Shared pydantic models
  input/         Gamepad observation service
  protocol/      64-bit ESP32 frame encoding/decoding
  sensors/       BMX055 pipeline, MCP3208 ADC, contact detection, replay
  simulation/    MuJoCo digital twin (gait, pneumatic actuator, virtual IMU)
  transport/     Serial gateway (real PySerial + emulated stub)
config.py        All settings (HIGHEND_* env / .env overridable)
scripts/         Operations CLIs (see Scripts table)
tests/           Unit and API tests (pytest, no hardware needed)
web-vue/         Production Vue 3 UI
Motion/          Gait CSVs (Fixed Motion committed, Custom Motion local)
config/          Machine-local calibration/connection files (gitignored) + sim config
simulation/      MuJoCo XML model
docs/            Operator guides, experiment records (docs/experiments/)
single-leg-app/  Standalone single-cylinder test bench app (own README)
```

## Serial transport

When `HIGHEND_EMULATE_DEVICES` is unset, the real `PySerialGateway` opens
`HIGHEND_FRONT_PORT_NAME` / `HIGHEND_BACK_PORT_NAME` (defaults
`/dev/ttyUSB-Front` / `/dev/ttyUSB-Back`). With `HIGHEND_EMULATE_DEVICES=true`
the stub gateway emulates the ESP32 pair: fake telemetry over WebSocket,
targets update the dummy actuators, and the dashboard renders the emulated
joint state.
