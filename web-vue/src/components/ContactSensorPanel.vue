<script setup lang="ts">
import { computed, reactive } from 'vue';

import Button from 'primevue/button';
import Card from 'primevue/card';
import Tag from 'primevue/tag';
import { useToast } from 'primevue/usetoast';

import RobotModelViewport from '@/components/RobotModelViewport.vue';
import type {
  ContactCalibration,
  ImuOrientation,
  ImuQuaternion,
  LegId,
  LegPreview,
  SensorState,
} from '@/types/control';
import type { ContactLegState } from '@/utils/contactSensors';
import { legLabel } from '@/utils/i18n';

const props = defineProps<{
  legs: LegPreview[];
  focusedLegId: LegId;
  sensors: SensorState | null;
  states: ContactLegState[];
  calibration: ContactCalibration | null;
  imuQuaternion?: ImuQuaternion | null;
  imuOrientation?: ImuOrientation | null;
}>();

const emit = defineEmits<{
  'update:focusedLegId': [legId: LegId];
  save: [calibration: ContactCalibration];
}>();

const toast = useToast();

// Per-leg captured raw values for the calibration wizard.
const captured = reactive<Record<string, { unloaded: number | null; loaded: number | null }>>({});
const saving = reactive({ busy: false });

const primaryBank = computed(
  () => props.sensors?.adc_banks.find((bank) => bank.device === 0) ?? props.sensors?.adc_banks[0] ?? null,
);
const supportingLegIds = computed(() =>
  props.states.filter((state) => state.supporting).map((state) => state.legId),
);

function capturedFor(legId: LegId): { unloaded: number | null; loaded: number | null } {
  if (!captured[legId]) {
    captured[legId] = { unloaded: null, loaded: null };
  }
  return captured[legId];
}

function currentRaw(legId: LegId): number | null {
  return props.states.find((state) => state.legId === legId)?.raw ?? null;
}

function capture(legId: LegId, kind: 'unloaded' | 'loaded'): void {
  const raw = currentRaw(legId);
  if (raw === null) {
    toast.add({ severity: 'warn', summary: 'ADC値がありません', life: 2500 });
    return;
  }
  capturedFor(legId)[kind] = raw;
}

const readyToSave = computed(
  () =>
    props.calibration !== null &&
    props.calibration.legs.every((leg) => {
      const values = captured[leg.leg];
      return values && values.unloaded !== null && values.loaded !== null;
    }),
);

function buildCalibration(): ContactCalibration | null {
  if (!props.calibration) return null;
  return {
    ...props.calibration,
    legs: props.calibration.legs.map((leg) => {
      const values = captured[leg.leg];
      if (!values || values.unloaded === null || values.loaded === null) {
        return leg;
      }
      const unloaded = values.unloaded;
      const loaded = values.loaded;
      const polarity = loaded >= unloaded ? 'active_high' : 'active_low';
      // Hysteresis band at 25% / 75% between the two captured levels.
      const on = Math.round(unloaded + (loaded - unloaded) * 0.75);
      const off = Math.round(unloaded + (loaded - unloaded) * 0.25);
      return { ...leg, polarity, on_threshold: on, off_threshold: off } as typeof leg;
    }),
  };
}

async function save(): Promise<void> {
  const next = buildCalibration();
  if (!next) return;
  saving.busy = true;
  try {
    emit('save', next);
  } finally {
    saving.busy = false;
  }
}

function formatVoltage(value: number | null): string {
  return value === null ? '-' : `${value.toFixed(3)} V`;
}
</script>

<template>
  <section class="contact-sensor-page">
    <div class="contact-page-header">
      <div>
        <p class="section-kicker">Ground Contact</p>
        <h2>Contact Sensors</h2>
      </div>
      <div class="contact-header-status">
        <Tag
          :severity="primaryBank?.connection_state === 'connected' ? 'success' : 'secondary'"
          :value="`MCP3208 CE${primaryBank?.device ?? 0}: ${primaryBank?.connection_state ?? 'disabled'}`"
        />
        <Tag
          :severity="supportingLegIds.length ? 'success' : 'contrast'"
          :value="`${supportingLegIds.length} supporting legs`"
        />
      </div>
    </div>

    <Card class="contact-settings-card">
      <template #title>脚別しきい値校正</template>
      <template #subtitle>
        各脚について「無荷重」（脚を浮かせる）と「荷重」（接地・体重をかける）の生値を記録し、
        ヒステリシス付きしきい値をサーバーへ保存します。判定はサーバー側（デバウンス付き）です。
      </template>
      <template #content>
        <div class="contact-calibration-grid">
          <div v-for="state in states" :key="state.legId" class="contact-calibration-row">
            <strong>{{ legLabel(state.legId) }}</strong>
            <span class="contact-calibration-raw">現在値: {{ state.raw ?? '-' }}</span>
            <Button
              size="small"
              severity="secondary"
              :label="`無荷重を記録 (${capturedFor(state.legId).unloaded ?? '-'})`"
              @click="capture(state.legId, 'unloaded')"
            />
            <Button
              size="small"
              severity="secondary"
              :label="`荷重を記録 (${capturedFor(state.legId).loaded ?? '-'})`"
              @click="capture(state.legId, 'loaded')"
            />
            <span v-if="calibration" class="contact-calibration-current">
              ON≥{{ calibration.legs.find((leg) => leg.leg === state.legId)?.on_threshold }} /
              OFF&lt;{{ calibration.legs.find((leg) => leg.leg === state.legId)?.off_threshold }}
            </span>
          </div>
        </div>
        <div class="contact-calibration-actions">
          <Button
            label="校正を保存"
            icon="pi pi-save"
            :disabled="!readyToSave || saving.busy"
            @click="save"
          />
          <p class="contact-safety-note">
            暫定マッピング: CH0=右前, CH1=左前, CH2=右後, CH3=左後。
            制御（接地ゲート・支持脚補正）で使う前に、この画面で全脚の反応を実機確認してください。
          </p>
        </div>
      </template>
    </Card>

    <div class="contact-main-grid">
      <Card class="contact-model-card">
        <template #title>Support visualization</template>
        <template #subtitle>Supporting legs are highlighted in green.</template>
        <template #content>
          <RobotModelViewport
            :legs="legs"
            :focused-leg-id="focusedLegId"
            :supporting-leg-ids="supportingLegIds"
            :imu-quaternion="imuQuaternion"
            :imu-orientation="imuOrientation"
          />
        </template>
      </Card>

      <div class="contact-leg-grid">
        <button
          v-for="state in states"
          :key="state.legId"
          type="button"
          class="contact-leg-card"
          :class="{
            'is-supporting': state.supporting,
            'is-focused': state.legId === focusedLegId,
          }"
          @click="emit('update:focusedLegId', state.legId)"
        >
          <span class="contact-leg-card-header">
            <strong>{{ legLabel(state.legId) }}</strong>
            <Tag
              :severity="state.supporting ? 'success' : 'secondary'"
              :value="state.supporting ? 'SUPPORT' : 'FREE'"
            />
          </span>
          <span class="contact-signal-value">{{ state.raw ?? '-' }}</span>
          <span class="contact-channel-reading">
            <span>CH{{ state.channel ?? '-' }}</span>
            <strong>{{ state.raw ?? '-' }}</strong>
            <em>{{ formatVoltage(state.voltage) }}</em>
          </span>
        </button>
      </div>
    </div>

    <p v-if="primaryBank?.error" class="contact-error">{{ primaryBank.error }}</p>
  </section>
</template>
