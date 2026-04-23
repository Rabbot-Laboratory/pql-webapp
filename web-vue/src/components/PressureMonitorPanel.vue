<script setup lang="ts">
import Card from 'primevue/card';
import Tag from 'primevue/tag';

import type { ActuatorState } from '@/types/control';
import { actuatorLabel } from '@/utils/i18n';

defineProps<{
  actuators: ActuatorState[];
}>();
</script>

<template>
  <Card class="pressure-card">
    <template #title>Pressure Monitor</template>
    <template #subtitle>ESP から受信した 12bit の圧力生値を 8 軸分表示します。</template>

    <template #content>
      <div class="pressure-grid">
        <article v-for="actuator in actuators" :key="actuator.actuator_id" class="pressure-tile">
          <div class="pressure-tile-header">
            <strong>{{ actuatorLabel(actuator.label) }}</strong>
            <Tag severity="secondary" :value="`ID ${actuator.actuator_id}`" />
          </div>
          <div class="pressure-value-row">
            <span class="pressure-value">{{ actuator.telemetry.pressure }}</span>
            <span class="pressure-unit">raw / 4095</span>
          </div>
          <div class="pressure-meter">
            <div
              class="pressure-meter-fill"
              :style="{ width: `${Math.max(0, Math.min(100, (actuator.telemetry.pressure / 4095) * 100))}%` }"
            ></div>
          </div>
        </article>
      </div>
    </template>
  </Card>
</template>
