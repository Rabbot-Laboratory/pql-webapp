<script setup lang="ts">
import { computed } from 'vue';

import Button from 'primevue/button';
import Card from 'primevue/card';
import ProgressBar from 'primevue/progressbar';
import Tag from 'primevue/tag';

import ImuOrientationViewport from '@/components/ImuOrientationViewport.vue';
import type { ImuOrientation, ImuQuaternion, ImuVector, MagCalibrationQuality, SensorState } from '@/types/control';

// The backend has no fixed sample cap; this is only a UI reference point so the
// progress bar has something to move against while the user rotates the robot.
const MAG_CALIBRATION_NOMINAL_SAMPLES = 2000;

const props = defineProps<{
  sensors: SensorState | null;
  busy?: boolean;
  magCalibrationBusy?: boolean;
  magCalibrationQuality?: MagCalibrationQuality | null;
}>();

const emit = defineEmits<{
  calibrateLevel: [];
  calibrateGyro: [];
  resetCalibration: [];
  startMagCalibration: [];
  finishMagCalibration: [];
  cancelMagCalibration: [];
}>();

const magActive = computed(() => props.sensors?.imu.mag_calibration_active ?? false);
const magSampleCount = computed(() => props.sensors?.imu.mag_calibration_samples ?? 0);
const magProgress = computed(() =>
  Math.min(100, Math.round((magSampleCount.value / MAG_CALIBRATION_NOMINAL_SAMPLES) * 100)),
);
const magStartDisabled = computed(
  () => props.busy || magActive.value || props.magCalibrationBusy || props.sensors?.imu.connection_state !== 'connected',
);

function formatNumber(value: number | null | undefined, digits = 2): string {
  return value === null || value === undefined ? '-' : value.toFixed(digits);
}

function formatOrientation(orientation: ImuOrientation | null | undefined): string {
  if (!orientation) {
    return 'Roll - / Pitch - / Yaw -';
  }
  return [
    `Roll ${formatNumber(orientation.roll_deg)} deg`,
    `Pitch ${formatNumber(orientation.pitch_deg)} deg`,
    `Yaw ${formatNumber(orientation.yaw_deg)} deg`,
  ].join(' / ');
}

function formatQuaternion(quaternion: ImuQuaternion | null | undefined): string {
  if (!quaternion) {
    return 'W - / X - / Y - / Z -';
  }
  return [
    `W ${formatNumber(quaternion.w, 4)}`,
    `X ${formatNumber(quaternion.x, 4)}`,
    `Y ${formatNumber(quaternion.y, 4)}`,
    `Z ${formatNumber(quaternion.z, 4)}`,
  ].join(' / ');
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

          <ImuOrientationViewport
            :quaternion="sensors?.imu.quaternion ?? null"
            :orientation="sensors?.imu.orientation ?? null"
            :connection-state="sensors?.imu.connection_state ?? 'disabled'"
          />

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
            <article>
              <span>Gravity</span>
              <strong>{{ formatVector(sensors?.imu.gravity_g, 'g') }}</strong>
            </article>
            <article>
              <span>Linear Accel</span>
              <strong>{{ formatVector(sensors?.imu.linear_accel_g, 'g') }}</strong>
            </article>
            <article>
              <span>Quaternion</span>
              <strong>{{ formatQuaternion(sensors?.imu.quaternion) }}</strong>
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

          <article class="calibration-action-card">
            <h3>3. 磁気較正</h3>
            <p>ハード/ソフトアイアン誤差を補正し、ヨーの精度を改善します。</p>

            <Button
              v-if="!magActive"
              label="磁気較正開始"
              icon="pi pi-compass"
              severity="help"
              :loading="magCalibrationBusy"
              :disabled="magStartDisabled"
              @click="emit('startMagCalibration')"
            />

            <div v-else class="mag-calibration-progress">
              <p class="mag-calibration-instruction">ロボットを全方向にゆっくり回転させてください。</p>
              <ProgressBar :value="magProgress" />
              <span class="mag-calibration-samples">サンプル数: {{ magSampleCount }}</span>
              <div class="mag-calibration-buttons">
                <Button
                  label="完了"
                  icon="pi pi-check"
                  severity="success"
                  :loading="magCalibrationBusy"
                  :disabled="magCalibrationBusy"
                  @click="emit('finishMagCalibration')"
                />
                <Button
                  label="キャンセル"
                  icon="pi pi-times"
                  severity="danger"
                  outlined
                  :disabled="magCalibrationBusy"
                  @click="emit('cancelMagCalibration')"
                />
              </div>
            </div>

            <div v-if="!magActive && magCalibrationQuality" class="mag-quality-result">
              <span>前回の結果</span>
              <strong>
                サンプル {{ magCalibrationQuality.sample_count }} / 残差
                {{ formatNumber(magCalibrationQuality.residual, 3) }} / カバレッジ
                {{ (magCalibrationQuality.coverage * 100).toFixed(0) }}%
              </strong>
            </div>
          </article>

          <article class="calibration-action-card is-danger">
            <h3>リセット</h3>
            <p>保存済みの水平補正・ジャイロ補正・磁気較正を初期値に戻します。</p>
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
          <div>
            <span>Mag Offset</span>
            <strong>{{ formatVector(sensors?.imu.calibration.mag_offset, 'raw') }}</strong>
          </div>
          <div>
            <span>Mag Scale</span>
            <strong>{{ formatVector(sensors?.imu.calibration.mag_scale, '') }}</strong>
          </div>
        </section>
      </div>
    </template>
  </Card>
</template>
