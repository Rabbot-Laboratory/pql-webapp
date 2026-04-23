import type {
  ActuatorState,
  FixedMotion,
  HealthResponse,
  ImportedMotionDraft,
  LegPreview,
  MotionCategory,
  MotionFileDetail,
  MotionLibrarySnapshot,
  TelemetryRecordingScope,
  TelemetryRecordingStatus,
  TelemetryEvent,
} from '@/types/control';

async function readJson<T>(input: RequestInfo, init?: RequestInit): Promise<T> {
  const response = await fetch(input, init);
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export async function fetchHealth(): Promise<HealthResponse> {
  return readJson<HealthResponse>('/api/health');
}

export async function fetchActuators(): Promise<{ items: ActuatorState[] }> {
  return readJson<{ items: ActuatorState[] }>('/api/actuators');
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
    const data = JSON.parse(event.data) as TelemetryEvent;
    onMessage(data);
  });
  socket.addEventListener('close', onClose);
  return socket;
}
