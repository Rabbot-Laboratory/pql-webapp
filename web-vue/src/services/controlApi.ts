import type {
  ActuatorState,
  FixedMotion,
  HealthResponse,
  ImportedMotionDraft,
  LegPreview,
  MagCalibrationQuality,
  MotionCategory,
  MotionFileDetail,
  MotionLibrarySnapshot,
  SensorState,
  StabilizationGains,
  StabilizationState,
  TelemetryRecordingScope,
  TelemetryRecordingStatus,
  TelemetryEvent,
} from '@/types/control';

async function readJson<T>(input: RequestInfo, init?: RequestInit): Promise<T> {
  const response = await fetch(input, init);
  if (!response.ok) {
    let detail: string | undefined;
    try {
      const body = (await response.clone().json()) as { detail?: unknown };
      if (typeof body?.detail === 'string') {
        detail = body.detail;
      }
    } catch {
      // Response body was not JSON (or already consumed); fall back to a status-only message.
    }
    throw new Error(detail ? `HTTP ${response.status}: ${detail}` : `HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export async function fetchHealth(): Promise<HealthResponse> {
  return readJson<HealthResponse>('/api/health');
}

export async function fetchActuators(): Promise<{ items: ActuatorState[] }> {
  return readJson<{ items: ActuatorState[] }>('/api/actuators');
}

export async function fetchSensors(): Promise<{ item: SensorState }> {
  return readJson<{ item: SensorState }>('/api/sensors');
}

export async function calibrateImuLevel(): Promise<{ item: SensorState }> {
  return readJson<{ item: SensorState }>('/api/sensors/imu/calibration/level', {
    method: 'POST',
  });
}

export async function calibrateImuGyroZero(sampleCount = 60): Promise<{ item: SensorState }> {
  return readJson<{ item: SensorState }>('/api/sensors/imu/calibration/gyro-zero', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sample_count: sampleCount }),
  });
}

export async function resetImuCalibration(): Promise<{ item: SensorState }> {
  return readJson<{ item: SensorState }>('/api/sensors/imu/calibration/reset', {
    method: 'POST',
  });
}

export async function startMagCalibration(): Promise<{ item: SensorState }> {
  return readJson<{ item: SensorState }>('/api/sensors/imu/calibration/mag/start', {
    method: 'POST',
  });
}

export async function finishMagCalibration(): Promise<{ item: SensorState; quality: MagCalibrationQuality }> {
  return readJson<{ item: SensorState; quality: MagCalibrationQuality }>(
    '/api/sensors/imu/calibration/mag/finish',
    { method: 'POST' },
  );
}

export async function cancelMagCalibration(): Promise<{ item: SensorState }> {
  return readJson<{ item: SensorState }>('/api/sensors/imu/calibration/mag/cancel', {
    method: 'POST',
  });
}

export async function fetchStabilization(): Promise<StabilizationState> {
  return readJson<StabilizationState>('/api/control/stabilization');
}

export async function updateStabilization(payload: {
  enabled?: boolean;
  gains?: StabilizationGains;
}): Promise<StabilizationState> {
  return readJson<StabilizationState>('/api/control/stabilization', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

export async function fetchLegPreviews(): Promise<{ items: LegPreview[] }> {
  return readJson<{ items: LegPreview[] }>('/api/preview/legs');
}

export async function fetchMotionLibrary(): Promise<MotionLibrarySnapshot> {
  return readJson<MotionLibrarySnapshot>('/api/motions/library');
}

export async function fetchMotionFile(category: MotionCategory, name: string): Promise<MotionFileDetail> {
  return readJson<MotionFileDetail>(`/api/motions/library/${category}/${encodeURIComponent(name)}`);
}

export async function saveMotionFile(
  category: MotionCategory,
  payload: {
    name: string;
    rows: string[][];
    interval_sec: number;
    loop: boolean;
    advance_mode?: 'time' | 'guarded';
    position_tolerance?: number;
    pressure_threshold?: number;
    step_timeout_sec?: number;
    settle_time_sec?: number;
  },
): Promise<MotionFileDetail> {
  return readJson<MotionFileDetail>(`/api/motions/library/${category}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

export async function deleteMotionFile(category: MotionCategory, name: string): Promise<{ ok: boolean }> {
  return readJson<{ ok: boolean }>(`/api/motions/library/${category}/${encodeURIComponent(name)}`, {
    method: 'DELETE',
  });
}

export async function importLegacyCsv(payload: {
  name?: string;
  content: string;
}): Promise<ImportedMotionDraft> {
  return readJson<ImportedMotionDraft>('/api/motions/import/legacy-csv', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

export async function sendTarget(
  actuatorId: number,
  payload: { mode: 'position' | 'command'; value: number },
): Promise<{ item: ActuatorState }> {
  return readJson<{ item: ActuatorState }>(`/api/actuators/${actuatorId}/target`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

export async function sendGain(
  actuatorId: number,
  payload: { p: number; i: number; d: number },
): Promise<{ ok: boolean }> {
  return readJson<{ ok: boolean }>(`/api/actuators/${actuatorId}/gain`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

export async function requestGain(actuatorId: number): Promise<{ ok: boolean }> {
  return readJson<{ ok: boolean }>(`/api/actuators/${actuatorId}/gain/request`, {
    method: 'POST',
  });
}

export async function requestGainSave(actuatorId: number): Promise<{ ok: boolean }> {
  return readJson<{ ok: boolean }>(`/api/actuators/${actuatorId}/gain/save`, {
    method: 'POST',
  });
}

export async function requestCapture(
  actuatorId: number,
  payload: { capture: 'offset' | 'stroke' },
): Promise<{ ok: boolean }> {
  return readJson<{ ok: boolean }>(`/api/actuators/${actuatorId}/capture`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

export async function startFixedMotion(payload: { motion: FixedMotion }): Promise<{ ok: boolean }> {
  return readJson<{ ok: boolean }>('/api/motions/fixed', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

export async function startCsvPlayback(payload: {
  rows: string[][];
  interval_sec: number;
  loop: boolean;
  motion_name?: string;
  motion_category?: MotionCategory;
  advance_mode?: 'time' | 'guarded';
  position_tolerance?: number;
  pressure_threshold?: number;
  step_timeout_sec?: number;
  settle_time_sec?: number;
}): Promise<{ ok: boolean }> {
  return readJson<{ ok: boolean }>('/api/csv/playback/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

export async function stopCsvPlayback(): Promise<{ ok: boolean }> {
  return readJson<{ ok: boolean }>('/api/csv/playback/stop', {
    method: 'POST',
  });
}

export async function fetchTelemetryRecordingStatus(): Promise<TelemetryRecordingStatus> {
  return readJson<TelemetryRecordingStatus>('/api/telemetry/recording');
}

export async function startTelemetryRecording(payload?: {
  scope?: TelemetryRecordingScope;
  actuator_id?: number | null;
}): Promise<TelemetryRecordingStatus> {
  return readJson<TelemetryRecordingStatus>('/api/telemetry/recording/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload ?? {}),
  });
}

export async function stopTelemetryRecording(): Promise<TelemetryRecordingStatus> {
  return readJson<TelemetryRecordingStatus>('/api/telemetry/recording/stop', {
    method: 'POST',
  });
}

export function latestTelemetryRecordingDownloadUrl(): string {
  return '/api/telemetry/recording/latest';
}

export function createWebSocket(
  onMessage: (event: TelemetryEvent) => void,
  onClose: () => void,
): WebSocket {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const socket = new WebSocket(`${protocol}//${window.location.host}/api/ws`);
  socket.addEventListener('message', (event) => {
    let data: TelemetryEvent;
    try {
      data = JSON.parse(event.data) as TelemetryEvent;
    } catch (error) {
      console.warn('[ws] failed to parse message', error);
      return;
    }
    onMessage(data);
  });
  socket.addEventListener('close', onClose);
  return socket;
}
