<script setup lang="ts">
import Button from 'primevue/button';
import Card from 'primevue/card';
import Tag from 'primevue/tag';

import type { ImuOrientation, ImuVector, SensorState } from '@/types/control';

defineProps<{
  sensors: SensorState | null;
  busy?: boolean;
}>();

const emit = defineEmits<{
  calibrateLevel: [];
  calibrateGyro: [];
  resetCalibration: [];
}>();

function formatNumber(value: number | null | undefined, digits = 2): string {
  return value === null || value === undefined ? '-' : value.toFixed(digits);
}

function formatOrientation(orientation: ImuOrientation | null | undefined): string {
  if (!orientation) {
    return 'Roll - / Pitch -';
  }
  return `Roll ${formatNumber(orientation.roll_deg)} deg / Pitch ${formatNumber(orientation.pitch_deg)} deg`;
}

function formatVector(vector: ImuVector | null | undefined, unit: string): string {
  if (!vector) {
    return `X - / Y - / Z - ${unit}`;
  }
  return [
    `X ${formatNumber(vector.x, 3)}`,
    `Y ${formatNumber(vector.y, 3)}`,
    `Z ${formatNumber(vector.z, 3)} ${unit}`,
  ].join(' / ');
}
</script>

<template>
  <Card class="sensor-calibration-card">
    <template #title>Sensor Calibration</template>
    <template #subtitle>IMUを水平基準とジャイロゼロで補正します。補正値はPi側に保存されます。</template>

    <template #content>
      <div class="sensor-calibration-layout">
        <section class="sensor-calibration-status">
          <div class="sensor-calibration-header">
            <div>
              <h3>BMX055 IMU</h3>
              <p>現在姿勢と生データ</p>
            </div>
            <Tag
              :severity="sensors?.imu.connection_state === 'connected' ? 'success' : 'secondary'"
              :value="sensors?.imu.connection_state ?? 'disabled'"
            />
          </div>

          <div class="orientation-readout">
            <span>補正後</span>
            <strong>{{ formatOrientation(sensors?.imu.orientation) }}</strong>
          </div>
          <div class="orientation-readout is-muted">
            <span>Raw</span>
            <strong>{{ formatOrientation(sensors?.imu.raw_orientation) }}</strong>
          </div>

          <div class="sensor-raw-grid">
            <article>
              <span>Accel</span>
              <strong>{{ formatVector(sensors?.imu.accel_g, 'g') }}</strong>
            </article>
            <article>
              <span>Gyro</span>
              <strong>{{ formatVector(sensors?.imu.gyro_dps, 'deg/s') }}</strong>
            </article>
            <article>
              <span>Mag</span>
              <strong>{{ formatVector(sensors?.imu.mag_raw, 'raw') }}</strong>
            </article>
          </div>

          <p v-if="sensors?.imu.error" class="sensor-error-text">{{ sensors.imu.error }}</p>
        </section>

        <section class="sensor-calibration-actions">
          <article class="calibration-action-card">
            <h3>1. 水平ゼロ</h3>
            <p>ロボットを基準姿勢に置き、現在のRoll/Pitchを0度として保存します。</p>
            <Button
              label="現在姿勢を水平として保存"
              icon="pi pi-compass"
              :loading="busy"
              :disabled="busy || sensors?.imu.connection_state !== 'connected'"
              @click="emit('calibrateLevel')"
            />
          </article>

          <article class="calibration-action-card">
            <h3>2. ジャイロゼロ</h3>
            <p>完全に静止させた状態で数秒平均し、ドリフト分を保存します。</p>
            <Button
              label="静止ジャイロをゼロ保存"
              icon="pi pi-stopwatch"
              severity="info"
              :loading="busy"
              :disabled="busy || sensors?.imu.connection_state !== 'connected'"
              @click="emit('calibrateGyro')"
            />
          </article>

          <article class="calibration-action-card is-danger">
            <h3>リセット</h3>
            <p>保存済みの水平補正とジャイロ補正を初期値に戻します。</p>
            <Button
              label="IMU補正をリセット"
              icon="pi pi-refresh"
              severity="danger"
              outlined
              :loading="busy"
              :disabled="busy"
              @click="emit('resetCalibration')"
            />
          </article>
        </section>

        <section class="calibration-values">
          <h3>保存中の補正値</h3>
          <div>
            <span>Level Roll</span>
            <strong>{{ formatNumber(sensors?.imu.calibration.level_roll_deg) }} deg</strong>
          </div>
          <div>
            <span>Level Pitch</span>
            <strong>{{ formatNumber(sensors?.imu.calibration.level_pitch_deg) }} deg</strong>
          </div>
          <div>
            <span>Gyro Offset</span>
            <strong>{{ formatVector(sensors?.imu.calibration.gyro_offset_dps, 'deg/s') }}</strong>
          </div>
        </section>
      </div>
    </template>
  </Card>
</template>
