import { computed, ref, shallowRef } from 'vue';
import { defineStore } from 'pinia';

import {
  createWebSocket,
  deleteMotionFile,
  fetchActuators,
  fetchHealth,
  fetchLegPreviews,
  fetchTelemetryRecordingStatus,
  fetchMotionFile,
  fetchMotionLibrary,
  importLegacyCsv,
  latestTelemetryRecordingDownloadUrl,
  requestCapture,
  requestGain,
  requestGainSave,
  saveMotionFile,
  sendGain,
  sendTarget,
  startTelemetryRecording,
  startCsvPlayback,
  startFixedMotion,
  stopTelemetryRecording,
  stopCsvPlayback,
} from '@/services/controlApi';
import type {
  ActuatorState,
  ControlMode,
  FixedMotion,
  ImportedMotionDraft,
  LegId,
  LegPreview,
  PlaybackAdvanceMode,
  MotionCategory,
  MotionFileDetail,
  MotionLibrarySnapshot,
  SystemStatus,
  TelemetryEvent,
  TelemetryRecordingScope,
  TelemetryRecordingStatus,
  TelemetrySample,
} from '@/types/control';

type WsState = 'connecting' | 'live' | 'disconnected' | 'error';

const HISTORY_LIMIT = 120;
const UI_FLUSH_INTERVAL_MS = 40;

function legIdForActuator(actuatorId: number): LegId {
  if (actuatorId <= 1) return 'front_right';
  if (actuatorId <= 3) return 'front_left';
  if (actuatorId <= 5) return 'rear_right';
  return 'rear_left';
}

function sampleFromActuator(actuator: ActuatorState): TelemetrySample {
  return {
    timestamp: actuator.updated_at,
    position: actuator.telemetry.position,
    voltage: actuator.telemetry.voltage,
    command: actuator.telemetry.command,
    pressure: actuator.telemetry.pressure,
    target_position: actuator.target_position,
    target_command: actuator.target_command,
  };
}

export const useControlStore = defineStore('control', () => {
  const system = ref<SystemStatus | null>(null);
  const actuators = shallowRef<ActuatorState[]>([]);
  const legs = shallowRef<LegPreview[]>([]);
  const selectedActuatorHistory = ref<TelemetrySample[]>([]);
  const actuatorHistories = shallowRef<Record<number, TelemetrySample[]>>({});
  const activeTab = ref('dashboard');
  const focusedLegId = ref<LegId>('front_right');
  const selectedActuatorId = ref(0);
  const wsState = ref<WsState>('connecting');
  const loading = ref(false);
  const socket = ref<WebSocket | null>(null);
  const motionLibrary = ref<MotionLibrarySnapshot>({ fixed: [], custom: [] });
  const telemetryRecording = ref<TelemetryRecordingStatus>({
    is_recording: false,
    current_log_name: null,
    latest_log_name: null,
    started_at: null,
    sample_count: 0,
    scope: 'all',
    actuator_id: null,
  });

  let reconnectTimer: number | null = null;
  let flushTimer: number | null = null;
  const pendingActuators = new Map<number, ActuatorState>();
  const pendingLegs = new Map<LegId, LegPreview>();
  let pendingSystem: SystemStatus | null = null;

  const focusedLeg = computed(() => legs.value.find((item) => item.leg_id === focusedLegId.value) ?? null);
  const selectedActuator = computed(
    () => actuators.value.find((item) => item.actuator_id === selectedActuatorId.value) ?? null,
  );
  const connectedActuatorCount = computed(() => actuators.value.length);

  function appendActuatorHistory(actuator: ActuatorState): void {
    const sample = sampleFromActuator(actuator);
    actuatorHistories.value = {
      ...actuatorHistories.value,
      [actuator.actuator_id]: [...(actuatorHistories.value[actuator.actuator_id] ?? []), sample].slice(-HISTORY_LIMIT),
    };
  }

  function appendSelectedHistory(actuator: ActuatorState): void {
    appendActuatorHistory(actuator);
    if (actuator.actuator_id !== selectedActuatorId.value) {
      return;
    }
    selectedActuatorHistory.value = [...selectedActuatorHistory.value, sampleFromActuator(actuator)].slice(-HISTORY_LIMIT);
  }

  function resetSelectedHistory(actuator: ActuatorState | null): void {
    selectedActuatorHistory.value = actuator ? [sampleFromActuator(actuator)] : [];
  }

  function resetActuatorHistories(actuatorList: ActuatorState[]): void {
    actuatorHistories.value = Object.fromEntries(
      actuatorList.map((actuator) => [actuator.actuator_id, [sampleFromActuator(actuator)]]),
    );
  }

  function syncSelectedTargets(): void {
    if (!actuators.value.length) {
      return;
    }
    if (!actuators.value.find((item) => item.actuator_id === selectedActuatorId.value)) {
      selectedActuatorId.value = actuators.value[0].actuator_id;
    }
    if (!legs.value.find((item) => item.leg_id === focusedLegId.value) && legs.value[0]) {
      focusedLegId.value = legs.value[0].leg_id;
    }
    resetSelectedHistory(selectedActuator.value);
  }

  function upsertActuatorLocal(next: ActuatorState, appendHistory = false): void {
    const nextList = [...actuators.value];
    const index = nextList.findIndex((item) => item.actuator_id === next.actuator_id);
    if (index === -1) {
      nextList.push(next);
      nextList.sort((left, right) => left.actuator_id - right.actuator_id);
    } else {
      nextList[index] = next;
    }
    actuators.value = nextList;
    if (appendHistory) {
      appendSelectedHistory(next);
    }
  }

  function scheduleFlush(): void {
    if (flushTimer !== null) {
      return;
    }
    flushTimer = window.setTimeout(() => {
      flushTimer = null;
      flushPending();
    }, UI_FLUSH_INTERVAL_MS);
  }

  function flushPending(): void {
    if (pendingSystem) {
      system.value = pendingSystem;
      pendingSystem = null;
    }

    if (pendingActuators.size) {
      const nextList = [...actuators.value];
      for (const actuator of pendingActuators.values()) {
        const index = nextList.findIndex((item) => item.actuator_id === actuator.actuator_id);
        if (index === -1) {
          nextList.push(actuator);
        } else {
          nextList[index] = actuator;
        }
        appendSelectedHistory(actuator);
      }
      nextList.sort((left, right) => left.actuator_id - right.actuator_id);
      actuators.value = nextList;
      pendingActuators.clear();
    }

    if (pendingLegs.size) {
      const nextList = [...legs.value];
      for (const leg of pendingLegs.values()) {
        const index = nextList.findIndex((item) => item.leg_id === leg.leg_id);
        if (index === -1) {
          nextList.push(leg);
        } else {
          nextList[index] = leg;
        }
      }
      legs.value = nextList;
      pendingLegs.clear();
    }
  }

  function handleWsMessage(event: TelemetryEvent): void {
    if (event.type === 'snapshot') {
      const payload = event.payload as {
        system: SystemStatus;
        actuators: ActuatorState[];
        legs: LegPreview[];
      };
      system.value = payload.system;
      telemetryRecording.value = {
        ...telemetryRecording.value,
        is_recording: payload.system.telemetry_recording,
        current_log_name: payload.system.telemetry_log_name,
        scope: payload.system.telemetry_recording_scope,
        actuator_id: payload.system.telemetry_recording_actuator_id,
      };
      actuators.value = payload.actuators;
      legs.value = payload.legs;
      resetActuatorHistories(payload.actuators);
      pendingActuators.clear();
      pendingLegs.clear();
      syncSelectedTargets();
      return;
    }

    if (event.type === 'server_status') {
      pendingSystem = event.payload as SystemStatus;
      telemetryRecording.value = {
        ...telemetryRecording.value,
        is_recording: pendingSystem.telemetry_recording,
        current_log_name: pendingSystem.telemetry_log_name,
        scope: pendingSystem.telemetry_recording_scope,
        actuator_id: pendingSystem.telemetry_recording_actuator_id,
      };
      scheduleFlush();
      return;
    }

    if (event.type === 'csv_playback_status') {
      const payload = event.payload as { status: SystemStatus['playback_status'] };
      if (system.value) {
        system.value = {
          ...system.value,
          playback_status: payload.status,
        };
      }
      if (pendingSystem) {
        pendingSystem = {
          ...pendingSystem,
          playback_status: payload.status,
        };
      }
      return;
    }

    if (event.type === 'motion_library') {
      motionLibrary.value = event.payload as MotionLibrarySnapshot;
      return;
    }

    if (event.type === 'telemetry' || event.type === 'actuator_state' || event.type === 'gain_response') {
      const actuator = (event.payload as { actuator: ActuatorState }).actuator;
      pendingActuators.set(actuator.actuator_id, actuator);
      scheduleFlush();
      return;
    }

    if (event.type === 'leg_preview') {
      const leg = (event.payload as { leg: LegPreview }).leg;
      pendingLegs.set(leg.leg_id, leg);
      scheduleFlush();
    }
  }

  async function refresh(): Promise<void> {
    loading.value = true;
    try {
      const [health, actuatorSnapshot, legSnapshot, librarySnapshot, recordingStatus] = await Promise.all([
        fetchHealth(),
        fetchActuators(),
        fetchLegPreviews(),
        fetchMotionLibrary(),
        fetchTelemetryRecordingStatus(),
      ]);
      system.value = health.system;
      actuators.value = actuatorSnapshot.items;
      legs.value = legSnapshot.items;
      resetActuatorHistories(actuatorSnapshot.items);
      motionLibrary.value = librarySnapshot;
      telemetryRecording.value = recordingStatus;
      syncSelectedTargets();
    } finally {
      loading.value = false;
    }
  }

  function scheduleReconnect(): void {
    if (reconnectTimer !== null) {
      window.clearTimeout(reconnectTimer);
    }
    reconnectTimer = window.setTimeout(() => {
      connectWebSocket();
    }, 2000);
  }

  function connectWebSocket(): void {
    socket.value?.close();
    wsState.value = 'connecting';
    socket.value = createWebSocket(
      (event) => {
        wsState.value = 'live';
        handleWsMessage(event);
      },
      () => {
        wsState.value = 'disconnected';
        scheduleReconnect();
      },
    );
    socket.value.addEventListener('open', () => {
      wsState.value = 'live';
    });
    socket.value.addEventListener('error', () => {
      wsState.value = 'error';
    });
  }

  async function initialize(): Promise<void> {
    await refresh();
    connectWebSocket();
  }

  function dispose(): void {
    if (reconnectTimer !== null) {
      window.clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (flushTimer !== null) {
      window.clearTimeout(flushTimer);
      flushTimer = null;
    }
    socket.value?.close();
    socket.value = null;
  }

  function selectActuator(actuatorId: number): void {
    selectedActuatorId.value = actuatorId;
    focusedLegId.value = legIdForActuator(actuatorId);
    resetSelectedHistory(selectedActuator.value);
  }

  function selectLeg(legId: LegId): void {
    focusedLegId.value = legId;
    const preferredActuator = {
      front_right: 0,
      front_left: 2,
      rear_right: 4,
      rear_left: 6,
    }[legId];
    selectedActuatorId.value = preferredActuator;
    resetSelectedHistory(selectedActuator.value);
  }

  async function submitTarget(actuator: ActuatorState, mode: ControlMode, value: number): Promise<void> {
    upsertActuatorLocal({
      ...actuator,
      target_position: mode === 'position' ? value : actuator.target_position,
      target_command: mode === 'command' ? value : actuator.target_command,
      updated_at: new Date().toISOString(),
    });
    const response = await sendTarget(actuator.actuator_id, { mode, value });
    upsertActuatorLocal(response.item);
  }

  async function submitGain(actuator: ActuatorState, payload: { p: number; i: number; d: number }): Promise<void> {
    await sendGain(actuator.actuator_id, payload);
    upsertActuatorLocal({
      ...actuator,
      gains: {
        p: payload.p,
        i: payload.i,
        d: payload.d,
      },
      updated_at: new Date().toISOString(),
    });
  }

  async function reloadGain(actuator: ActuatorState): Promise<void> {
    await requestGain(actuator.actuator_id);
  }

  async function saveGain(actuator: ActuatorState, payload: { p: number; i: number; d: number }): Promise<void> {
    await submitGain(actuator, payload);
    await requestGainSave(actuator.actuator_id);
  }

  async function saveCapture(actuator: ActuatorState): Promise<void> {
    await requestGainSave(actuator.actuator_id);
  }

  async function capture(actuator: ActuatorState, captureType: 'offset' | 'stroke'): Promise<void> {
    await requestCapture(actuator.actuator_id, { capture: captureType });
  }

  async function triggerFixedMotion(motion: FixedMotion): Promise<void> {
    await startFixedMotion({ motion });
  }

  async function startPlayback(
    rows: string[][],
    intervalSec: number,
    loop: boolean,
    options?: {
      motionName?: string;
      motionCategory?: MotionCategory;
      advanceMode?: PlaybackAdvanceMode;
      positionTolerance?: number;
      pressureThreshold?: number;
      stepTimeoutSec?: number;
      settleTimeSec?: number;
    },
  ): Promise<void> {
    await startCsvPlayback({
      rows,
      interval_sec: intervalSec,
      loop,
      motion_name: options?.motionName,
      motion_category: options?.motionCategory,
      advance_mode: options?.advanceMode,
      position_tolerance: options?.positionTolerance,
      pressure_threshold: options?.pressureThreshold,
      step_timeout_sec: options?.stepTimeoutSec,
      settle_time_sec: options?.settleTimeSec,
    });
  }

  async function stopPlayback(): Promise<void> {
    await stopCsvPlayback();
  }

  async function activateFreeMode(): Promise<void> {
    const currentActuators = [...actuators.value].sort((left, right) => left.actuator_id - right.actuator_id);
    await Promise.all(currentActuators.map((actuator) => submitTarget(actuator, 'command', 0)));
  }

  async function refreshMotionLibrary(): Promise<void> {
    motionLibrary.value = await fetchMotionLibrary();
  }

  async function loadMotionFile(category: MotionCategory, name: string): Promise<MotionFileDetail> {
    return fetchMotionFile(category, name);
  }

  async function importLegacyCsvDraft(name: string | undefined, content: string): Promise<ImportedMotionDraft> {
    return importLegacyCsv({ name, content });
  }

  async function saveMotion(
    category: MotionCategory,
    name: string,
    rows: string[][],
    intervalSec: number,
    loop: boolean,
    advanceMode: PlaybackAdvanceMode = 'time',
    positionTolerance = 160,
    pressureThreshold = 0,
    stepTimeoutSec = 1.5,
    settleTimeSec = 0.1,
  ): Promise<MotionFileDetail> {
    const detail = await saveMotionFile(category, {
      name,
      rows,
      interval_sec: intervalSec,
      loop,
      advance_mode: advanceMode,
      position_tolerance: positionTolerance,
      pressure_threshold: pressureThreshold,
      step_timeout_sec: stepTimeoutSec,
      settle_time_sec: settleTimeSec,
    });
    await refreshMotionLibrary();
    return detail;
  }

  async function deleteMotion(category: MotionCategory, name: string): Promise<void> {
    await deleteMotionFile(category, name);
    await refreshMotionLibrary();
  }

  async function refreshTelemetryRecording(): Promise<void> {
    telemetryRecording.value = await fetchTelemetryRecordingStatus();
  }

  async function beginTelemetryRecording(scope: TelemetryRecordingScope, actuatorId?: number): Promise<void> {
    telemetryRecording.value = await startTelemetryRecording({
      scope,
      actuator_id: scope === 'selected' ? actuatorId ?? null : null,
    });
    if (system.value) {
      system.value = {
        ...system.value,
        telemetry_recording: telemetryRecording.value.is_recording,
        telemetry_log_name: telemetryRecording.value.current_log_name,
        telemetry_recording_scope: telemetryRecording.value.scope,
        telemetry_recording_actuator_id: telemetryRecording.value.actuator_id,
      };
    }
  }

  async function endTelemetryRecording(): Promise<void> {
    telemetryRecording.value = await stopTelemetryRecording();
    if (system.value) {
      system.value = {
        ...system.value,
        telemetry_recording: telemetryRecording.value.is_recording,
        telemetry_log_name: telemetryRecording.value.current_log_name,
        telemetry_recording_scope: telemetryRecording.value.scope,
        telemetry_recording_actuator_id: telemetryRecording.value.actuator_id,
      };
    }
  }

  function downloadLatestTelemetryRecording(): void {
    window.open(latestTelemetryRecordingDownloadUrl(), '_blank', 'noopener,noreferrer');
  }

  return {
    activeTab,
    actuators,
    actuatorHistories,
    capture,
    connectedActuatorCount,
    dispose,
    focusedLeg,
    focusedLegId,
    importLegacyCsvDraft,
    initialize,
    legs,
    loading,
    loadMotionFile,
    motionLibrary,
    telemetryRecording,
    refresh,
    refreshTelemetryRecording,
    refreshMotionLibrary,
    reloadGain,
    saveGain,
    deleteMotion,
    saveMotion,
    saveCapture,
    selectActuator,
    selectLeg,
    selectedActuator,
    selectedActuatorHistory,
    selectedActuatorId,
    beginTelemetryRecording,
    endTelemetryRecording,
    downloadLatestTelemetryRecording,
    submitGain,
    submitTarget,
    startPlayback,
    activateFreeMode,
    triggerFixedMotion,
    stopPlayback,
    system,
    wsState,
  };
});
