import type { ActuatorState, ControlMode, SystemStatus, TelemetryEvent } from '@/types/control';

async function readJson<T>(input: RequestInfo, init?: RequestInit): Promise<T> {
  const response = await fetch(input, init);
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: string };
      detail = body.detail ?? detail;
    } catch {
      // Keep the status fallback for non-JSON responses.
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export function fetchHealth(): Promise<{ system: SystemStatus }> {
  return readJson('/api/health');
}

export function fetchActuators(): Promise<{ items: ActuatorState[] }> {
  return readJson('/api/actuators');
}

export function sendTarget(
  actuatorId: number,
  mode: ControlMode,
  value: number,
): Promise<{ item: ActuatorState }> {
  return readJson(`/api/actuators/${actuatorId}/target`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode, value }),
  });
}

export function setGain(actuatorId: number, p: number, i: number, d: number): Promise<{ ok: boolean }> {
  return readJson(`/api/actuators/${actuatorId}/gain`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ p, i, d }),
  });
}

export function requestGain(actuatorId: number, save = false): Promise<{ ok: boolean }> {
  return readJson(`/api/actuators/${actuatorId}/gain/${save ? 'save' : 'request'}`, {
    method: 'POST',
  });
}

export function requestCapture(
  actuatorId: number,
  capture: 'offset' | 'stroke',
): Promise<{ ok: boolean }> {
  return readJson(`/api/actuators/${actuatorId}/capture`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ capture }),
  });
}

export function openTelemetrySocket(
  onMessage: (event: TelemetryEvent) => void,
  onClose: () => void,
): WebSocket {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const socket = new WebSocket(`${protocol}//${window.location.host}/api/ws`);
  socket.addEventListener('message', (message) => {
    try {
      onMessage(JSON.parse(message.data) as TelemetryEvent);
    } catch {
      // Ignore malformed serial/WebSocket input without surfacing sensor warnings.
    }
  });
  socket.addEventListener('close', onClose);
  return socket;
}
