<script setup lang="ts">
import Card from 'primevue/card';
import Tag from 'primevue/tag';

import type { ActuatorState, ImuVector, SensorState } from '@/types/control';
import { actuatorLabel } from '@/utils/i18n';

defineProps<{
  actuators: ActuatorState[];
  sensors: SensorState | null;
}>();

function formatNumber(value: number | null | undefined, digits = 3): string {
  return value === null || value === undefined ? '-' : value.toFixed(digits);
}

function formatVector(vector: ImuVector | null, unit: string): string {
  if (!vector) {
    return `- ${unit}`;
  }
  return `X ${formatNumber(vector.x)} / Y ${formatNumber(vector.y)} / Z ${formatNumber(vector.z)} ${unit}`;
}
</script>

<template>
  <Card class="pressure-card">
    <template #title>Pressure / Sensors</template>

    <template #content>
      <section class="sensor-section">
        <div class="sensor-section-header">
          <h3>Pi Sensors</h3>
          <span :class="['sensor-state-pill', sensors?.enabled ? 'is-enabled' : 'is-disabled']">
            {{ sensors?.enabled ? 'enabled' : 'disabled' }}
          </span>
        </div>

        <div class="sensor-grid">
          <article class="sensor-tile">
            <div class="sensor-tile-header">
              <strong>BMX055 IMU</strong>
              <Tag
                :severity="sensors?.imu.connection_state === 'connected' ? 'success' : 'secondary'"
                :value="sensors?.imu.connection_state ?? 'disabled'"
              />
            </div>
            <p>{{ formatVector(sensors?.imu.accel_g ?? null, 'g') }}</p>
            <p>{{ formatVector(sensors?.imu.gyro_dps ?? null, 'deg/s') }}</p>
            <p>{{ formatVector(sensors?.imu.mag_raw ?? null, 'raw') }}</p>
            <small v-if="sensors?.imu.error">{{ sensors.imu.error }}</small>
          </article>

          <article v-for="bank in sensors?.adc_banks ?? []" :key="`${bank.bus}-${bank.device}`" class="sensor-tile">
            <div class="sensor-tile-header">
              <strong>MCP3204 CE{{ bank.device }}</strong>
              <Tag
                :severity="bank.connection_state === 'connected' ? 'success' : 'secondary'"
                :value="bank.connection_state"
              />
            </div>
            <div class="adc-channel-list">
              <div v-for="channel in bank.channels" :key="channel.channel" class="adc-channel-row">
                <span>CH{{ channel.channel }}</span>
                <strong>{{ channel.raw ?? '-' }}</strong>
                <em>{{ formatNumber(channel.voltage, 3) }} V</em>
              </div>
            </div>
            <small v-if="bank.error">{{ bank.error }}</small>
          </article>
        </div>
      </section>

      <section class="sensor-section">
        <div class="sensor-section-header">
          <h3>ESP Pressure</h3>
        </div>

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
      </section>
    </template>
  </Card>
</template>
