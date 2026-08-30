import { computed, ref, shallowRef } from 'vue';
import { defineStore } from 'pinia';

import {
  addExperimentNote,
  calibrateImuGyroZero,
  calibrateImuLevel,
  cancelMagCalibration as apiCancelMagCalibration,
  createWebSocket,
  deleteMotionFile,
  fetchAdaptiveWalk,
  fetchContactCalibration,
  fetchExperimentStatus,
  listExperiments,
  startExperiment,
  stopExperiment,
  updateContactCalibration,
  fetchActuators,
  fetchHealth,
  fetchGamepad,
  fetchHardwareStatus,
  fetchLegPreviews,
  fetchSensors,
  fetchStabilization,
  fetchStanding,
  fetchTelemetryRecordingStatus,
  fetchMotionFile,
  fetchMotionLibrary,
  finishMagCalibration as apiFinishMagCalibration,
  importLegacyCsv,
  latestTelemetryRecordingDownloadUrl,
  moveToHome,
  requestCapture,
  requestGain,
  requestGainSave,
  resetImuCalibration,
  saveMotionFile,
  setAdaptiveForward,
  sendGain,
  sendTarget,
  startMagCalibration as apiStartMagCalibration,
  startTelemetryRecording,
  startCsvPlayback,
  startFixedMotion,
  stopTelemetryRecording,
  stopCsvPlayback,
  updateStabilization,
  updateStanding,
} from '@/services/controlApi';
import type {
  AdaptiveWalkMode,
  AdaptiveWalkState,
  ActuatorState,
  ContactCalibration,
  ControlMode,
  ExperimentManifest,
  ExperimentStatus,
  ExperimentSummary,
  FixedMotion,
  GamepadState,
  HardwareStatus,
  WebGamepadUpdate,
  ImportedMotionDraft,
  LegId,
  LegPreview,
  MagCalibrationQuality,
  PlaybackAdvanceMode,
  MotionCategory,
  MotionFileDetail,
  MotionLibrarySnapshot,
  SensorState,
  StabilizationGains,
  StabilizationState,
  StandingState,
  SystemStatus,
  TelemetryEvent,
  TelemetryRecordingScope,
  TelemetryRecordingStatus,
  TelemetrySample,
} from '@/types/control';
import { deriveContactLegStates } from '@/utils/contactSensors';

type WsState = 'connecting' | 'live' | 'disconnected' | 'error';

const HISTORY_LIMIT = 120;
const UI_FLUSH_INTERVAL_MS = 40;
// Rolling window for the gait diagram: ~25 s at the 20 Hz sensor push rate.
const GAIT_HISTORY_LIMIT = 512;

export interface GaitSample {
  t: number;
  phase: number | null;
  gateWaiting: boolean;
  walking: boolean;
  contacts: Record<LegId, boolean>;
}

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
  const sensors = ref<SensorState | null>(null);
  const gamepad = ref<GamepadState | null>(null);
  const hardware = ref<HardwareStatus | null>(null);
  const contactCalibration = ref<ContactCalibration | null>(null);
  const gaitHistory = shallowRef<GaitSample[]>([]);
  const experiments = ref<ExperimentManifest[]>([]);
  const experimentRunning = ref<ExperimentManifest | null>(null);
  // Live recorder status: recordings started by the API or by a
  // cycle-bounded walk must show up here too, not just GUI-started ones.
  const experimentStatus = ref<ExperimentStatus | null>(null);
  const lastExperimentSummary = ref<ExperimentSummary | null>(null);
  const stabilization = ref<StabilizationState | null>(null);
  const standing = ref<StandingState | null>(null);
  const adaptiveWalk = ref<AdaptiveWalkState | null>(null);
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
  // Stale-response guards: if a newer request for the same shared state fires before an
  // older one's response lands (e.g. rapid toggle/apply clicks), only the response matching
  // the latest request id is allowed to write the shared ref.
  let stabilizationRequestId = 0;
  let magCalibrationRequestId = 0;
  const pendingActuators = new Map<number, ActuatorState>();
  const pendingLegs = new Map<LegId, LegPreview>();
  let pendingSystem: SystemStatus | null = null;
  // Latest-wins buffer for `sensor_state` (can arrive at up to sensor push rate). Flushed on
  // the same UI_FLUSH_INTERVAL_MS cadence as actuator/leg updates instead of writing
  // `sensors.value` directly, so IMU updates don't force a three.js re-render on every packet.
  let pendingSensors: SensorState | null = null;

  const focusedLeg = computed(() => legs.value.find((item) => item.leg_id === focusedLegId.value) ?? null);
  const selectedActuator = computed(
    () => actuators.value.find((item) => item.actuator_id === selectedActuatorId.value) ?? null,
  );
  const connectedActuatorCount = computed(() => actuators.value.length);
  const contactLegStates = computed(() =>
    deriveContactLegStates(sensors.value, contactCalibration.value),
  );
  const supportingLegIds = computed(() =>
    contactLegStates.value.filter((state) => state.supporting).map((state) => state.legId),
  );

  async function saveContactCalibration(calibration: ContactCalibration): Promise<void> {
    const response = await updateContactCalibration(calibration);
    contactCalibration.value = calibration;
    sensors.value = response.item;
  }

  function appendGaitSample(sensorState: SensorState): void {
    const contacts = Object.fromEntries(
      sensorState.contact.map((state) => [state.leg, state.supporting]),
    ) as Record<LegId, boolean>;
    const walk = adaptiveWalk.value;
    const sample: GaitSample = {
      t: Date.now(),
      phase: walk?.active ? walk.phase : null,
      gateWaiting: walk?.gate_waiting ?? false,
      walking: walk?.active ?? false,
      contacts,
    };
    gaitHistory.value = [...gaitHistory.value, sample].slice(-GAIT_HISTORY_LIMIT);
  }

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

    if (pendingSensors) {
      sensors.value = pendingSensors;
      appendGaitSample(pendingSensors);
      pendingSensors = null;
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
        system?: SystemStatus;
        actuators?: ActuatorState[];
        legs?: LegPreview[];
        sensors?: SensorState;
        stabilization?: StabilizationState;
        standing?: StandingState;
        experiment?: ExperimentStatus;
        adaptive_walk?: AdaptiveWalkState;
        gamepad?: GamepadState;
        hardware?: HardwareStatus;
      } | null;
      // Defensive guard: a malformed/partial snapshot payload must not write `undefined`
      // into these typed refs (components dereference them without further null checks).
      if (!payload?.system || !payload.actuators || !payload.legs) {
        return;
      }
      system.value = payload.system;
      sensors.value = payload.sensors ?? null;
      stabilization.value = payload.stabilization ?? null;
      standing.value = payload.standing ?? null;
      experimentStatus.value = payload.experiment ?? null;
      adaptiveWalk.value = payload.adaptive_walk ?? null;
      gamepad.value = payload.gamepad ?? null;
      hardware.value = payload.hardware ?? null;
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
      pendingSensors = null;
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

    if (event.type === 'sensor_state') {
      const sensorPayload = (event.payload as { sensors?: SensorState } | null)?.sensors;
      if (sensorPayload) {
        pendingSensors = sensorPayload;
        scheduleFlush();
      }
      return;
    }

    if (event.type === 'gamepad_state') {
      const gamepadPayload = (event.payload as { gamepad?: GamepadState } | null)?.gamepad;
      if (gamepadPayload) {
        gamepad.value = gamepadPayload;
      }
      return;
    }

    if (event.type === 'hardware_status') {
      const hardwarePayload = (event.payload as { hardware?: HardwareStatus } | null)?.hardware;
      if (hardwarePayload) {
        hardware.value = hardwarePayload;
      }
      return;
    }

    if (event.type === 'stabilization_state') {
      // Server already throttles this to ~8Hz (plus transition events), and the
      // stabilization panel doesn't drive any three.js work, so apply it directly
      // instead of routing through the UI_FLUSH_INTERVAL_MS buffer.
      const stabilizationPayload = (event.payload as { stabilization?: StabilizationState } | null)?.stabilization;
      if (stabilizationPayload) {
        stabilization.value = stabilizationPayload;
      }
      return;
    }

    if (event.type === 'experiment_state') {
      const payload = (event.payload as { experiment?: ExperimentStatus } | null)?.experiment;
      if (payload) {
        experimentStatus.value = payload;
        void refreshExperiments().catch(() => undefined);
      }
      return;
    }

    if (event.type === 'standing_state') {
      const standingPayload = (event.payload as { standing?: StandingState } | null)?.standing;
      if (standingPayload) {
        standing.value = standingPayload;
      }
      return;
    }

    if (event.type === 'adaptive_walk_state') {
      const walkingPayload = (event.payload as { adaptive_walk?: AdaptiveWalkState } | null)?.adaptive_walk;
      if (walkingPayload) {
        adaptiveWalk.value = walkingPayload;
      }
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
      const [health, actuatorSnapshot, legSnapshot, librarySnapshot, recordingStatus, sensorSnapshot, stabilizationSnapshot, adaptiveWalkSnapshot, standingSnapshot, gamepadSnapshot, hardwareSnapshot] =
        await Promise.all([
          fetchHealth(),
          fetchActuators(),
          fetchLegPreviews(),
          fetchMotionLibrary(),
          fetchTelemetryRecordingStatus(),
          fetchSensors(),
          fetchStabilization(),
          fetchAdaptiveWalk(),
          fetchStanding(),
          fetchGamepad(),
          fetchHardwareStatus(),
        ]);
      system.value = health.system;
      actuators.value = actuatorSnapshot.items;
      legs.value = legSnapshot.items;
      resetActuatorHistories(actuatorSnapshot.items);
      motionLibrary.value = librarySnapshot;
      sensors.value = sensorSnapshot.item;
      stabilization.value = stabilizationSnapshot;
      adaptiveWalk.value = adaptiveWalkSnapshot;
      standing.value = standingSnapshot;
      gamepad.value = gamepadSnapshot.item;
      hardware.value = hardwareSnapshot.item;
      telemetryRecording.value = recordingStatus;
      syncSelectedTargets();
      // Secondary state: never block the main snapshot on these.
      void fetchContactCalibration()
        .then((response) => {
          contactCalibration.value = response.item;
        })
        .catch(() => undefined);
      void refreshExperiments().catch(() => undefined);
      void fetchExperimentStatus()
        .then((status) => {
          experimentStatus.value = status;
        })
        .catch(() => undefined);
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

  function sendWebGamepadUpdate(update: WebGamepadUpdate): boolean {
    const currentSocket = socket.value;
    if (!currentSocket || currentSocket.readyState !== WebSocket.OPEN) {
      return false;
    }
    currentSocket.send(JSON.stringify({ type: 'gamepad_input', payload: update }));
    return true;
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
    adaptiveWalk.value = await setAdaptiveForward({
      pressed: false,
      safety_confirmed: false,
    });
    await stopCsvPlayback();
  }

  async function setForwardPressed(
    pressed: boolean,
    safetyConfirmed: boolean,
    options?: { cycles?: number | null; mode?: AdaptiveWalkMode; motionName?: string | null },
  ): Promise<void> {
    adaptiveWalk.value = await setAdaptiveForward({
      pressed,
      safety_confirmed: safetyConfirmed,
      cycles: options?.cycles ?? null,
      mode: options?.mode ?? 'adaptive',
      motion_name: options?.motionName ?? null,
    });
  }

  async function setStandingEnabled(enabled: boolean, safetyConfirmed = false): Promise<void> {
    standing.value = await updateStanding({
      enabled,
      safety_confirmed: safetyConfirmed,
    });
  }

  async function setStandingManualOk(value: boolean): Promise<void> {
    standing.value = await updateStanding({ enabled: true, manual_ok: value });
  }

  async function refreshExperiments(): Promise<void> {
    const response = await listExperiments();
    experiments.value = response.experiments;
    experimentRunning.value = response.experiments.find((item) => !item.ended_at) ?? null;
  }

  async function beginExperiment(experimentType: string, name?: string): Promise<void> {
    experimentRunning.value = await startExperiment({
      experiment_type: experimentType,
      name: name || null,
    });
    await refreshExperiments();
  }

  async function endExperiment(): Promise<void> {
    lastExperimentSummary.value = await stopExperiment();
    experimentRunning.value = null;
    await refreshExperiments();
  }

  async function noteExperiment(text: string): Promise<void> {
    await addExperimentNote(text);
  }

  async function moveHome(safetyConfirmed: boolean): Promise<void> {
    await moveToHome({ safety_confirmed: safetyConfirmed });
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

  async function setImuLevelCalibration(): Promise<void> {
    const response = await calibrateImuLevel();
    sensors.value = response.item;
  }

  async function setImuGyroZeroCalibration(sampleCount = 60): Promise<void> {
    const response = await calibrateImuGyroZero(sampleCount);
    sensors.value = response.item;
  }

  async function clearImuCalibration(): Promise<void> {
    const response = await resetImuCalibration();
    sensors.value = response.item;
  }

  async function startMagCalibration(): Promise<void> {
    const requestId = ++magCalibrationRequestId;
    const response = await apiStartMagCalibration();
    if (requestId === magCalibrationRequestId) {
      sensors.value = response.item;
    }
  }

  async function finishMagCalibration(): Promise<MagCalibrationQuality> {
    const requestId = ++magCalibrationRequestId;
    const response = await apiFinishMagCalibration();
    if (requestId === magCalibrationRequestId) {
      sensors.value = response.item;
    }
    return response.quality;
  }

  async function cancelMagCalibration(): Promise<void> {
    const requestId = ++magCalibrationRequestId;
    const response = await apiCancelMagCalibration();
    if (requestId === magCalibrationRequestId) {
      sensors.value = response.item;
    }
  }

  async function setStabilizationEnabled(enabled: boolean): Promise<void> {
    const requestId = ++stabilizationRequestId;
    const response = await updateStabilization({ enabled });
    if (requestId === stabilizationRequestId) {
      stabilization.value = response;
    }
  }

  async function applyStabilizationGains(gains: StabilizationGains): Promise<void> {
    const requestId = ++stabilizationRequestId;
    const response = await updateStabilization({ gains });
    if (requestId === stabilizationRequestId) {
      stabilization.value = response;
    }
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
    adaptiveWalk,
    actuators,
    actuatorHistories,
    applyStabilizationGains,
    cancelMagCalibration,
    capture,
    beginExperiment,
    connectedActuatorCount,
    contactCalibration,
    contactLegStates,
    dispose,
    endExperiment,
    experimentRunning,
    experimentStatus,
    experiments,
    gaitHistory,
    lastExperimentSummary,
    noteExperiment,
    refreshExperiments,
    saveContactCalibration,
    finishMagCalibration,
    focusedLeg,
    focusedLegId,
    gamepad,
    hardware,
    importLegacyCsvDraft,
    initialize,
    legs,
    loading,
    loadMotionFile,
    motionLibrary,
    moveHome,
    sensors,
    setForwardPressed,
    setStandingEnabled,
    setStandingManualOk,
    standing,
    setStabilizationEnabled,
    stabilization,
    startMagCalibration,
    telemetryRecording,
    refresh,
    refreshTelemetryRecording,
    refreshMotionLibrary,
    reloadGain,
    saveGain,
    deleteMotion,
    saveMotion,
    saveCapture,
    sendWebGamepadUpdate,
    selectActuator,
    selectLeg,
    selectedActuator,
    selectedActuatorHistory,
    selectedActuatorId,
    setImuGyroZeroCalibration,
    setImuLevelCalibration,
    beginTelemetryRecording,
    clearImuCalibration,
    endTelemetryRecording,
    downloadLatestTelemetryRecording,
    submitGain,
    submitTarget,
    startPlayback,
    activateFreeMode,
    triggerFixedMotion,
    stopPlayback,
    system,
    supportingLegIds,
    wsState,
  };
});
