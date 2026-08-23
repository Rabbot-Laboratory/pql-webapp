<script setup lang="ts">
import { computed } from 'vue';

import Card from 'primevue/card';
import InputNumber from 'primevue/inputnumber';
import SelectButton from 'primevue/selectbutton';
import Tag from 'primevue/tag';

import RobotModelViewport from '@/components/RobotModelViewport.vue';
import type {
  ImuOrientation,
  ImuQuaternion,
  LegId,
  LegPreview,
  SensorState,
} from '@/types/control';
import type { ContactLegState, ContactPolarity } from '@/utils/contactSensors';
import { legLabel } from '@/utils/i18n';

const props = defineProps<{
  legs: LegPreview[];
  focusedLegId: LegId;
  sensors: SensorState | null;
  states: ContactLegState[];
  threshold: number;
  polarity: ContactPolarity;
  imuQuaternion?: ImuQuaternion | null;
  imuOrientation?: ImuOrientation | null;
}>();

const emit = defineEmits<{
  'update:focusedLegId': [legId: LegId];
  'update:threshold': [value: number];
  'update:polarity': [value: ContactPolarity];
}>();

const polarityOptions: Array<{ label: string; value: ContactPolarity }> = [
  { label: 'Active High', value: 'active_high' },
  { label: 'Active Low', value: 'active_low' },
];

const primaryBank = computed(
  () => props.sensors?.adc_banks.find((bank) => bank.device === 0) ?? props.sensors?.adc_banks[0] ?? null,
);
const supportingLegIds = computed(() =>
  props.states.filter((state) => state.supporting).map((state) => state.legId),
);

function updateThreshold(value: number | null): void {
  if (value !== null) {
    emit('update:threshold', value);
  }
}

function updatePolarity(value: ContactPolarity): void {
  emit('update:polarity', value);
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
      <template #content>
        <div class="contact-settings-row">
          <label class="field compact-field">
            <span>Contact threshold [raw]</span>
            <InputNumber
              :model-value="threshold"
              :min="0"
              :max="4095"
              :step="16"
              @update:model-value="updateThreshold"
            />
          </label>
          <div class="field">
            <span>Detection polarity</span>
            <SelectButton
              :model-value="polarity"
              :options="polarityOptions"
              option-label="label"
              option-value="value"
              @update:model-value="updatePolarity"
            />
          </div>
          <p class="contact-safety-note">
            Display only: CH0=front right, CH1=front left, CH2=rear right,
            CH3=rear left. CH4-7 are spare. Verify this mapping on the robot before control use.
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
          <span class="contact-signal-value">{{ state.signalRaw ?? '-' }}</span>
          <span
            v-for="reading in state.readings"
            :key="reading.channel"
            class="contact-channel-reading"
          >
            <span>CH{{ reading.channel }}</span>
            <strong>{{ reading.raw ?? '-' }}</strong>
            <em>{{ formatVoltage(reading.voltage) }}</em>
          </span>
        </button>
      </div>
    </div>

    <p v-if="primaryBank?.error" class="contact-error">{{ primaryBank.error }}</p>
  </section>
</template>
