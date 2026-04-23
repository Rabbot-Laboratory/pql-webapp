<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue';

import Button from 'primevue/button';
import Card from 'primevue/card';
import InputNumber from 'primevue/inputnumber';
import Slider from 'primevue/slider';
import SplitButton from 'primevue/splitbutton';
import Tag from 'primevue/tag';

import TelemetryTrendChart from '@/components/TelemetryTrendChart.vue';
import type { ActuatorState, ControlMode, TelemetryRecordingScope, TelemetrySample } from '@/types/control';
import { actuatorLabel, portRoleLabel } from '@/utils/i18n';

const props = defineProps<{
  actuator: ActuatorState | null;
  samples: TelemetrySample[];
  busy?: boolean;
  compact?: boolean;
  telemetryRecording?: boolean;
  telemetryLogName?: string | null;
  telemetryRecordingScope?: TelemetryRecordingScope;
  telemetryRecordingActuatorId?: number | null;
}>();

const emit = defineEmits<{
  target: [actuator: ActuatorState, mode: ControlMode, value: number];
  'save-gain': [actuator: ActuatorState, payload: { p: number; i: number; d: number }];
  'save-capture': [actuator: ActuatorState];
  'reload-gain': [actuator: ActuatorState];
  capture: [actuator: ActuatorState, capture: 'offset' | 'stroke'];
  'start-recording': [scope: TelemetryRecordingScope, actuatorId?: number];
  'stop-recording': [];
  'download-recording': [];
}>();

const positionTarget = ref(2048);
const commandTarget = ref(900);
const pGain = ref<number | null>(null);
const iGain = ref<number | null>(null);
const dGain = ref<number | null>(null);
const pGainInput = ref<InstanceType<typeof InputNumber> | null>(null);
const iGainInput = ref<InstanceType<typeof InputNumber> | null>(null);
const dGainInput = ref<InstanceType<typeof InputNumber> | null>(null);
const gainDraftDirty = ref(false);
const pendingGainSave = ref<string | null>(null);
const activeActuatorId = computed(() => props.actuator?.actuator_id ?? -1);

let positionTimer: number | null = null;
let commandTimer: number | null = null;
let positionInteractionUntil = 0;
let commandInteractionUntil = 0;
let lastActuatorId = -1;

const recordingMenuItems = computed(() => {
  const actuatorId = props.actuator?.actuator_id;
  return [
    {
      label: '選択軸だけ記録',
      command: () => {
        if (actuatorId === undefined) {
          return;
        }
        emit('start-recording', 'selected', actuatorId);
      },
    },
  ];
});

const recordingScopeLabel = computed(() => {
  if (!props.telemetryRecording) {
    return null;
  }
  if (props.telemetryRecordingScope === 'selected') {
    return `記録範囲: 選択軸 ${props.telemetryRecordingActuatorId ?? '-'}`;
  }
  return '記録範囲: 全8軸';
});

function clearTimers(): void {
  if (positionTimer !== null) {
    window.clearTimeout(positionTimer);
    positionTimer = null;
  }
  if (commandTimer !== null) {
    window.clearTimeout(commandTimer);
    commandTimer = null;
  }
}

function syncDraftFromActuator(actuator: ActuatorState | null, force = false): void {
  const now = Date.now();
  if (force || now >= positionInteractionUntil) {
    positionTarget.value = actuator?.target_position ?? 2048;
  }
  if (force || now >= commandInteractionUntil) {
    commandTarget.value = actuator?.target_command ?? 900;
  }
  if (force || !gainDraftDirty.value) {
    pGain.value = actuator?.gains.p ?? 0;
    iGain.value = actuator?.gains.i ?? 0;
    dGain.value = actuator?.gains.d ?? 0;
  }
}

watch(
  activeActuatorId,
  (actuatorId) => {
    if (actuatorId !== lastActuatorId) {
      clearTimers();
      lastActuatorId = actuatorId;
      positionInteractionUntil = 0;
      commandInteractionUntil = 0;
      gainDraftDirty.value = false;
      pendingGainSave.value = null;
      syncDraftFromActuator(props.actuator, true);
    }
  },
  { immediate: true },
);

watch(
  () => [
    props.actuator?.target_position,
    props.actuator?.target_command,
    props.actuator?.gains.p,
    props.actuator?.gains.i,
    props.actuator?.gains.d,
  ] as const,
  () => {
    const signature = `${props.actuator?.gains.p ?? 0}:${props.actuator?.gains.i ?? 0}:${props.actuator?.gains.d ?? 0}`;
    if (pendingGainSave.value && signature === pendingGainSave.value) {
      gainDraftDirty.value = false;
      pendingGainSave.value = null;
    }
    syncDraftFromActuator(props.actuator, false);
  },
);

function scheduleTarget(mode: ControlMode, value: number, delayMs: number): void {
  const scheduledActuator = props.actuator;
  if (!scheduledActuator) {
    return;
  }

  const currentTimer = mode === 'position' ? positionTimer : commandTimer;
  if (currentTimer !== null) {
    window.clearTimeout(currentTimer);
  }

  const handle = window.setTimeout(() => {
    emit('target', scheduledActuator, mode, value);
    if (mode === 'position') {
      positionTimer = null;
    } else {
      commandTimer = null;
    }
  }, delayMs);

  if (mode === 'position') {
    positionTimer = handle;
  } else {
    commandTimer = handle;
  }
}

function updatePositionTarget(value: number | null | undefined): void {
  positionTarget.value = value ?? 0;
  positionInteractionUntil = Date.now() + 500;
  scheduleTarget('position', positionTarget.value, 24);
}

function updateCommandTarget(value: number | null | undefined): void {
  commandTarget.value = value ?? 0;
  commandInteractionUntil = Date.now() + 500;
  scheduleTarget('command', commandTarget.value, 24);
}

function commitPendingGainInput(): void {
  const activeElement = document.activeElement as HTMLElement | null;
  if (!activeElement) {
    return;
  }

  const inputs = [pGainInput.value, iGainInput.value, dGainInput.value];
  for (const input of inputs) {
    const element = input?.$refs?.input?.$el as HTMLInputElement | undefined;
    if (element && activeElement === element) {
      element.blur();
      return;
    }
  }
}

function onGainInput(key: 'p' | 'i' | 'd', value: number | null | undefined): void {
  const nextValue = value ?? 0;
  gainDraftDirty.value = true;
  pendingGainSave.value = null;

  if (key === 'p') {
    pGain.value = nextValue;
    return;
  }
  if (key === 'i') {
    iGain.value = nextValue;
    return;
  }
  dGain.value = nextValue;
}

function requestReloadGain(): void {
  if (!props.actuator) {
    return;
  }
  gainDraftDirty.value = false;
  pendingGainSave.value = null;
  syncDraftFromActuator(props.actuator, true);
  emit('reload-gain', props.actuator);
}

async function submitGain(): Promise<void> {
  if (!props.actuator) {
    return;
  }

  commitPendingGainInput();
  await nextTick();

  const payload = {
    p: Number(pGain.value ?? 0),
    i: Number(iGain.value ?? 0),
    d: Number(dGain.value ?? 0),
  };

  pendingGainSave.value = `${payload.p}:${payload.i}:${payload.d}`;
  gainDraftDirty.value = true;
  emit('save-gain', props.actuator, payload);
}

function saveCapture(): void {
  if (!props.actuator) {
    return;
  }
  emit('save-capture', props.actuator);
}

onBeforeUnmount(() => {
  clearTimers();
});
</script>

<template>
  <Card class="control-panel-card" :class="{ 'is-compact': compact }">
    <template #title>
      {{ actuator ? actuatorLabel(actuator.label) : 'アクチュエータを選択してください' }}
      <span v-if="actuator" class="p-card-subtitle inline-subtitle">
        {{ portRoleLabel(actuator.port_role) }} / Local {{ actuator.local_index }}
      </span>
    </template>
    <template #subtitle>
      <span v-if="!actuator">左の一覧から対象アクチュエータを選ぶと操作パネルを表示します。</span>
    </template>

    <template #content>
      <div v-if="actuator" class="control-panel">
        <div class="control-summary-grid">
          <div class="summary-chip summary-chip-contrast">
            <span class="summary-chip-label">Position</span>
            <strong class="summary-chip-value">{{ actuator.telemetry.position }}</strong>
          </div>
          <div class="summary-chip summary-chip-info">
            <span class="summary-chip-label">Voltage</span>
            <strong class="summary-chip-value">{{ actuator.telemetry.voltage }}</strong>
          </div>
          <div class="summary-chip summary-chip-success">
            <span class="summary-chip-label">Command</span>
            <strong class="summary-chip-value">{{ actuator.telemetry.command }}</strong>
          </div>
          <div class="summary-chip summary-chip-secondary">
            <span class="summary-chip-label">Pressure</span>
            <strong class="summary-chip-value">{{ actuator.telemetry.pressure }}</strong>
          </div>
          <div class="summary-chip summary-chip-warn">
            <span class="summary-chip-label">Offset</span>
            <strong class="summary-chip-value">{{ actuator.capture.min ?? '-' }}</strong>
          </div>
          <div class="summary-chip summary-chip-warn">
            <span class="summary-chip-label">Stroke</span>
            <strong class="summary-chip-value">{{ actuator.capture.max ?? '-' }}</strong>
          </div>
        </div>

        <section class="control-block flex-row-slider">
          <div class="control-block-header slider-header-row">
            <h3 style="width: 7.5rem;">Position Target</h3>
            <Slider class="flex-1 mx-3" :model-value="positionTarget" :min="0" :max="4095" @update:model-value="updatePositionTarget" />
            <strong style="width: 3rem; text-align: right; font-size: 1.1rem;">{{ positionTarget }}</strong>
          </div>
        </section>

        <section class="control-block flex-row-slider">
          <div class="control-block-header slider-header-row">
            <h3 style="width: 7.5rem;">Command Target</h3>
            <Slider class="flex-1 mx-3" :model-value="commandTarget" :min="0" :max="1800" @update:model-value="updateCommandTarget" />
            <strong style="width: 3rem; text-align: right; font-size: 1.1rem;">{{ commandTarget }}</strong>
          </div>
        </section>

        <section class="control-block">
          <div class="control-block-header">
            <h3>ゲイン</h3>
            <div class="inline-actions">
              <Button label="読込" size="small" severity="secondary" @click="requestReloadGain" />
              <Button label="反映して保存" size="small" :loading="busy" @click="submitGain" />
            </div>
          </div>
          <div class="gain-grid">
            <label class="field compact-field">
              <span>P</span>
              <InputNumber ref="pGainInput" v-model="pGain" :min="0" :max="255" @input="onGainInput('p', $event.value)" />
            </label>
            <label class="field compact-field">
              <span>I</span>
              <InputNumber ref="iGainInput" v-model="iGain" :min="0" :max="255" @input="onGainInput('i', $event.value)" />
            </label>
            <label class="field compact-field">
              <span>D</span>
              <InputNumber ref="dGainInput" v-model="dGain" :min="0" :max="255" @input="onGainInput('d', $event.value)" />
            </label>
          </div>
        </section>

        <section class="control-block">
          <div class="control-block-header">
            <h3>キャプチャ</h3>
            <div class="inline-actions">
              <Button label="Offset取得" size="small" severity="secondary" @click="$emit('capture', actuator, 'offset')" />
              <Button label="Stroke取得" size="small" severity="secondary" @click="$emit('capture', actuator, 'stroke')" />
              <Button label="キャプチャ保存" size="small" :loading="busy" @click="saveCapture" />
            </div>
          </div>
        </section>

        <section class="control-block">
          <div class="control-block-header">
            <h3>ライブグラフ</h3>
            <div class="inline-actions">
              <Tag v-if="telemetryRecording" severity="danger" value="REC" />
              <template v-if="telemetryRecording">
                <Button label="記録停止" size="small" severity="danger" @click="$emit('stop-recording')" />
              </template>
              <template v-else>
                <SplitButton
                  label="全8軸を記録"
                  size="small"
                  severity="secondary"
                  :model="recordingMenuItems"
                  @click="$emit('start-recording', 'all')"
                />
              </template>
              <Button
                label="CSV出力"
                size="small"
                severity="contrast"
                :disabled="!telemetryLogName"
                @click="$emit('download-recording')"
              />
            </div>
          </div>
          <p v-if="recordingScopeLabel" class="recording-caption">{{ recordingScopeLabel }}</p>
          <p v-if="telemetryLogName" class="recording-caption">保存先: {{ telemetryLogName }}</p>
          <TelemetryTrendChart :samples="samples" :compact="compact" />
        </section>
      </div>
    </template>
  </Card>
</template>

<style scoped>
.inline-subtitle {
  display: inline-block;
  margin-left: 0.5rem;
  vertical-align: baseline;
}
</style>
