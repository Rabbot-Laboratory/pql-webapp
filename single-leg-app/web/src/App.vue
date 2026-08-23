<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';

import Tab from 'primevue/tab';
import TabList from 'primevue/tablist';
import TabPanel from 'primevue/tabpanel';
import TabPanels from 'primevue/tabpanels';
import Tabs from 'primevue/tabs';
import Toast from 'primevue/toast';
import { useToast } from 'primevue/usetoast';

import ActuatorControlPanel from '@/components/ActuatorControlPanel.vue';
import ActuatorTable from '@/components/ActuatorTable.vue';
import FocusedLegView from '@/components/FocusedLegView.vue';
import RobotModelViewport from '@/components/RobotModelViewport.vue';
import SingleLegStatusToolbar from '@/components/SingleLegStatusToolbar.vue';
import {
  fetchActuators,
  fetchHealth,
  openTelemetrySocket,
  requestCapture,
  requestGain,
  sendTarget,
  setGain,
} from '@/api';
import type {
  ActuatorState,
  ControlMode,
  JointPreview,
  LegPreview,
  SystemStatus,
  TelemetryEvent,
  TelemetrySample,
} from '@/types/control';

const toast = useToast();
const actuators = ref<ActuatorState[]>([]);
const histories = ref<Record<number, TelemetrySample[]>>({});
const selectedActuatorId = ref(0);
const system = ref<SystemStatus | null>(null);
const loading = ref(false);
const wsState = ref<'connecting' | 'live' | 'disconnected' | 'error'>('connecting');
const isMobile = ref(false);

let socket: WebSocket | null = null;
let reconnectTimer: number | null = null;
const targetTimers = new Map<string, number>();

const selectedActuator = computed(
  () => actuators.value.find((item) => item.actuator_id === selectedActuatorId.value) ?? null,
);
const selectedHistory = computed(() => histories.value[selectedActuatorId.value] ?? []);
const jointActuators = computed(() =>
  ['hip', 'knee']
    .map((label) => actuators.value.find((item) => item.label === label))
    .filter((item): item is ActuatorState => item !== undefined),
);
const focusedLeg = computed<LegPreview[]>(() => {
  const hip = actuators.value.find((item) => item.label === 'hip');
  const knee = actuators.value.find((item) => item.label === 'knee');
  if (!hip || !knee) return [];
  return [
    {
      leg_id: 'front_right',
      label: 'Front Right',
      fixed_joint_name: 'rev_fr1',
      fixed_joint_angle_rad: 0,
      mirror_x: false,
      hip: jointPreview(hip, 'rev_fr2', -1),
      knee: jointPreview(knee, 'rev_fr3', 1),
      updated_at: hip.updated_at > knee.updated_at ? hip.updated_at : knee.updated_at,
    },
  ];
});

function jointPreview(actuator: ActuatorState, jointName: string, direction: 1 | -1): JointPreview {
  const travel = actuator.label === 'hip' ? 16 : 24;
  const angle = (position: number) => {
    const normalized = (Math.max(0, Math.min(4095, position)) - 2047.5) / 2047.5;
    return normalized * (travel * Math.PI / 180) * direction;
  };
  return {
    actuator_id: actuator.actuator_id,
    label: actuator.label,
    joint_name: jointName,
    position: actuator.telemetry.position,
    angle_rad: angle(actuator.telemetry.position),
    target_position: actuator.target_position,
    target_angle_rad: angle(actuator.target_position),
    command: actuator.telemetry.command,
  };
}

function sampleFrom(actuator: ActuatorState): TelemetrySample {
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

function resetHistories(items: ActuatorState[]): void {
  histories.value = Object.fromEntries(items.map((item) => [item.actuator_id, [sampleFrom(item)]]));
}

function upsertActuator(next: ActuatorState, appendHistory = true): void {
  const list = [...actuators.value];
  const index = list.findIndex((item) => item.actuator_id === next.actuator_id);
  if (index === -1) list.push(next);
  else list[index] = next;
  actuators.value = list.sort((left, right) => left.actuator_id - right.actuator_id);
  if (appendHistory) {
    histories.value = {
      ...histories.value,
      [next.actuator_id]: [...(histories.value[next.actuator_id] ?? []), sampleFrom(next)].slice(-120),
    };
  }
}

function handleEvent(event: TelemetryEvent): void {
  if (event.type === 'snapshot') {
    const payload = event.payload as unknown as { system: SystemStatus; actuators: ActuatorState[] };
    system.value = payload.system;
    actuators.value = payload.actuators;
    resetHistories(payload.actuators);
    return;
  }
  if (event.type === 'server_status') {
    system.value = event.payload as unknown as SystemStatus;
    return;
  }
  if (['telemetry', 'gain_response', 'actuator_state'].includes(event.type)) {
    const actuator = (event.payload as unknown as { actuator: ActuatorState }).actuator;
    if (actuator) upsertActuator(actuator);
  }
}

function connectSocket(): void {
  socket?.close();
  wsState.value = 'connecting';
  const nextSocket = openTelemetrySocket(handleEvent, () => {
    wsState.value = 'disconnected';
    if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
    reconnectTimer = window.setTimeout(connectSocket, 1500);
  });
  socket = nextSocket;
  nextSocket.addEventListener('open', () => {
    wsState.value = 'live';
  });
  nextSocket.addEventListener('error', () => {
    wsState.value = 'error';
  });
}

async function refreshSnapshot(): Promise<void> {
  loading.value = true;
  try {
    const [health, snapshot] = await Promise.all([fetchHealth(), fetchActuators()]);
    system.value = health.system;
    actuators.value = snapshot.items;
    resetHistories(snapshot.items);
  } catch (error) {
    toast.add({
      severity: 'error',
      summary: '更新に失敗しました',
      detail: error instanceof Error ? error.message : 'サーバーへ接続できません',
      life: 3000,
    });
  } finally {
    loading.value = false;
  }
}

function handleTarget(actuator: ActuatorState, mode: ControlMode, value: number): void {
  const key = `${actuator.actuator_id}:${mode}`;
  const existing = targetTimers.get(key);
  if (existing !== undefined) window.clearTimeout(existing);
  upsertActuator(
    {
      ...actuator,
      target_position: mode === 'position' ? value : actuator.target_position,
      target_command: mode === 'command' ? value : actuator.target_command,
    },
    false,
  );
  targetTimers.set(
    key,
    window.setTimeout(async () => {
      targetTimers.delete(key);
      try {
        const response = await sendTarget(actuator.actuator_id, mode, value);
        upsertActuator(response.item, false);
      } catch (error) {
        toast.add({
          severity: 'error',
          summary: 'ターゲット送信に失敗しました',
          detail: error instanceof Error ? error.message : '送信できませんでした',
          life: 3000,
        });
      }
    }, 35),
  );
}

async function handleGain(actuator: ActuatorState, gains: { p: number; i: number; d: number }): Promise<void> {
  try {
    await setGain(actuator.actuator_id, gains.p, gains.i, gains.d);
    await requestGain(actuator.actuator_id, true);
    toast.add({ severity: 'success', summary: 'ゲインを保存しました', detail: actuator.label, life: 1800 });
  } catch (error) {
    toast.add({ severity: 'error', summary: 'ゲイン保存に失敗しました', detail: String(error), life: 3000 });
  }
}

async function handleReloadGain(actuator: ActuatorState): Promise<void> {
  try {
    await requestGain(actuator.actuator_id);
  } catch (error) {
    toast.add({ severity: 'error', summary: 'ゲイン読込に失敗しました', detail: String(error), life: 3000 });
  }
}

async function handleCapture(actuator: ActuatorState, capture: 'offset' | 'stroke'): Promise<void> {
  try {
    await requestCapture(actuator.actuator_id, capture);
  } catch (error) {
    toast.add({ severity: 'error', summary: 'キャプチャに失敗しました', detail: String(error), life: 3000 });
  }
}

async function handleSaveCapture(actuator: ActuatorState): Promise<void> {
  try {
    await requestGain(actuator.actuator_id, true);
    toast.add({ severity: 'success', summary: 'キャプチャを保存しました', detail: actuator.label, life: 1800 });
  } catch (error) {
    toast.add({ severity: 'error', summary: '保存に失敗しました', detail: String(error), life: 3000 });
  }
}

function syncViewportMode(): void {
  isMobile.value = window.innerWidth <= 820;
}

onMounted(async () => {
  syncViewportMode();
  window.addEventListener('resize', syncViewportMode);
  await refreshSnapshot();
  connectSocket();
});

onBeforeUnmount(() => {
  window.removeEventListener('resize', syncViewportMode);
  socket?.close();
  if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
  targetTimers.forEach((timer) => window.clearTimeout(timer));
});
</script>

<template>
  <Toast position="top-right" />
  <div class="console-shell" :class="{ 'is-mobile-shell': isMobile }">
    <SingleLegStatusToolbar
      :system="system"
      :ws-state="wsState"
      :loading="loading"
      @refresh="refreshSnapshot"
    />

    <main class="console-main">
      <Tabs value="dashboard" class="h-full flex flex-col">
        <TabList>
          <Tab value="dashboard">Dashboard</Tab>
          <Tab value="model">3D View</Tab>
        </TabList>
        <TabPanels class="flex-1 overflow-hidden flex flex-col p-0">
          <TabPanel value="dashboard" class="flex-1 overflow-hidden">
            <section class="cockpit-layout h-full">
              <div
                class="cockpit-main-grid cockpit-main-grid-wide h-full overflow-hidden"
                :class="{ 'is-mobile-grid': isMobile }"
              >
                <div class="cockpit-side-stack h-full overflow-hidden flex flex-col">
                  <ActuatorTable
                    :actuators="actuators"
                    :histories="histories"
                    :loading="loading"
                    :selected-actuator-id="selectedActuatorId"
                    scroll-height="flex"
                    class="flex-1 min-h-0"
                    @select="selectedActuatorId = $event.actuator_id"
                  />
                </div>

                <ActuatorControlPanel
                  :actuator="selectedActuator"
                  :samples="selectedHistory"
                  :busy="loading"
                  compact
                  @target="handleTarget"
                  @save-gain="handleGain"
                  @save-capture="handleSaveCapture"
                  @reload-gain="handleReloadGain"
                  @capture="handleCapture"
                />

                <FocusedLegView
                  :focused-leg-id="'front_right'"
                  :legs="focusedLeg"
                  compact
                />
              </div>
            </section>
          </TabPanel>

          <TabPanel value="model" class="flex-1 overflow-hidden">
            <section class="model-focus-page">
              <div class="model-focus-stage">
                <RobotModelViewport
                  class="model-focus-viewport"
                  :legs="focusedLeg"
                  :focused-leg-id="'front_right'"
                />

                <div v-if="focusedLeg[0]" class="model-focus-readout">
                  <article>
                    <span>HIP / CH 0</span>
                    <strong>{{ focusedLeg[0].hip.position }}</strong>
                    <small>{{ ((focusedLeg[0].hip.angle_rad * 180) / Math.PI).toFixed(1) }}°</small>
                  </article>
                  <article>
                    <span>KNEE / CH 1</span>
                    <strong>{{ focusedLeg[0].knee.position }}</strong>
                    <small>{{ ((focusedLeg[0].knee.angle_rad * 180) / Math.PI).toFixed(1) }}°</small>
                  </article>
                </div>
              </div>

              <aside class="model-focus-sidebar">
                <div class="model-joint-switch" aria-label="操作する関節">
                  <button
                    v-for="actuator in jointActuators"
                    :key="actuator.actuator_id"
                    type="button"
                    :class="{ 'is-active': actuator.actuator_id === selectedActuatorId }"
                    @click="selectedActuatorId = actuator.actuator_id"
                  >
                    <span>{{ actuator.label.toUpperCase() }}</span>
                    <strong>{{ actuator.telemetry.position }}</strong>
                    <small>Target {{ actuator.target_position }}</small>
                  </button>
                </div>

                <ActuatorControlPanel
                  :actuator="selectedActuator"
                  :samples="selectedHistory"
                  :busy="loading"
                  compact
                  @target="handleTarget"
                  @save-gain="handleGain"
                  @save-capture="handleSaveCapture"
                  @reload-gain="handleReloadGain"
                  @capture="handleCapture"
                />
              </aside>
            </section>
          </TabPanel>
        </TabPanels>
      </Tabs>
    </main>
  </div>
</template>
