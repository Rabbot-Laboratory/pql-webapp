<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';

import ConfirmDialog from 'primevue/confirmdialog';
import Drawer from 'primevue/drawer';
import Tab from 'primevue/tab';
import TabList from 'primevue/tablist';
import TabPanel from 'primevue/tabpanel';
import TabPanels from 'primevue/tabpanels';
import Tabs from 'primevue/tabs';
import Toast from 'primevue/toast';
import { useConfirm } from 'primevue/useconfirm';
import { useToast } from 'primevue/usetoast';

import ActuatorControlPanel from '@/components/ActuatorControlPanel.vue';
import ActuatorTable from '@/components/ActuatorTable.vue';
import ContactSensorPanel from '@/components/ContactSensorPanel.vue';
import ExperimentPanel from '@/components/ExperimentPanel.vue';
import GaitDiagramPanel from '@/components/GaitDiagramPanel.vue';
import FocusedLegView from '@/components/FocusedLegView.vue';
import GamepadPanel from '@/components/GamepadPanel.vue';
import HardwareNotice from '@/components/HardwareNotice.vue';
import MotionControlPanel from '@/components/MotionControlPanel.vue';
import SensorCalibrationPanel from '@/components/SensorCalibrationPanel.vue';
import StabilizationPanel from '@/components/StabilizationPanel.vue';
import StandingPanel from '@/components/StandingPanel.vue';
import StatusToolbar from '@/components/StatusToolbar.vue';
import { useControlStore } from '@/stores/control';
import type {
  ActuatorState,
  ControlMode,
  FixedMotion,
  MagCalibrationQuality,
  MotionCategory,
  StabilizationGains,
} from '@/types/control';
import { actuatorLabel } from '@/utils/i18n';

const store = useControlStore();
const confirm = useConfirm();
const toast = useToast();
const navOpen = ref(false);
const isMobile = ref(false);
const sensorCalibrationBusy = ref(false);
const stabilizationToggleBusy = ref(false);
const stabilizationGainsBusy = ref(false);
const stabilizationGainsError = ref(false);
const magCalibrationBusy = ref(false);
const magCalibrationQuality = ref<MagCalibrationQuality | null>(null);
const homeBusy = ref(false);

const tabOptions = computed(() =>
  isMobile.value
    ? [
        { value: 'dashboard', label: '操作' },
        { value: 'legs', label: '脚' },
        { value: 'motion', label: '動作' },
        { value: 'contacts', label: '接地' },
        { value: 'controller', label: '操作入力' },
        { value: 'sensors', label: 'センサ・制御' },
      ]
    : [
        { value: 'dashboard', label: 'Dashboard' },
        { value: 'legs', label: 'Kinematics' },
        { value: 'motion', label: 'Motion' },
        { value: 'contacts', label: 'Contact Sensors' },
        { value: 'controller', label: 'Controller' },
        { value: 'sensors', label: 'Sensors & Control' },
      ],
);

function syncViewportMode(): void {
  isMobile.value = window.innerWidth <= 820;
}

async function refreshSnapshot(): Promise<void> {
  try {
    await store.refresh();
    toast.add({
      severity: 'success',
      summary: '更新しました',
      detail: '最新の状態を取得しました。',
      life: 1800,
    });
  } catch (error) {
    toast.add({
      severity: 'error',
      summary: '更新に失敗しました',
      detail: error instanceof Error ? error.message : '不明なエラーです。',
      life: 3500,
    });
  }
}

async function handleTarget(actuator: ActuatorState, mode: ControlMode, value: number): Promise<void> {
  try {
    await store.submitTarget(actuator, mode, value);
  } catch (error) {
    toast.add({
      severity: 'error',
      summary: 'ターゲット送信に失敗しました',
      detail: error instanceof Error ? error.message : '不明なエラーです。',
      life: 3000,
    });
  }
}

async function handleGain(actuator: ActuatorState, payload: { p: number; i: number; d: number }): Promise<void> {
  try {
    await store.saveGain(actuator, payload);
    toast.add({
      severity: 'success',
      summary: 'ゲインを保存しました',
      detail: `${actuatorLabel(actuator.label)}: P=${payload.p}, I=${payload.i}, D=${payload.d}`,
      life: 2200,
    });
  } catch (error) {
    toast.add({
      severity: 'error',
      summary: 'ゲイン保存に失敗しました',
      detail: error instanceof Error ? error.message : '不明なエラーです。',
      life: 3200,
    });
  }
}

async function handleReloadGain(actuator: ActuatorState): Promise<void> {
  try {
    await store.reloadGain(actuator);
    toast.add({
      severity: 'success',
      summary: 'ゲイン読込を要求しました',
      detail: actuatorLabel(actuator.label),
      life: 1800,
    });
  } catch (error) {
    toast.add({
      severity: 'error',
      summary: 'ゲイン読込に失敗しました',
      detail: error instanceof Error ? error.message : '不明なエラーです。',
      life: 3200,
    });
  }
}

async function handleCapture(actuator: ActuatorState, captureType: 'offset' | 'stroke'): Promise<void> {
  try {
    await store.capture(actuator, captureType);
    toast.add({
      severity: 'success',
      summary: `${captureType === 'offset' ? 'Offset' : 'Stroke'} を取得しました`,
      detail: actuatorLabel(actuator.label),
      life: 1800,
    });
  } catch (error) {
    toast.add({
      severity: 'error',
      summary: 'キャプチャに失敗しました',
      detail: error instanceof Error ? error.message : '不明なエラーです。',
      life: 3200,
    });
  }
}

async function handleSaveCapture(actuator: ActuatorState): Promise<void> {
  try {
    await store.saveCapture(actuator);
    toast.add({
      severity: 'success',
      summary: 'キャプチャを保存しました',
      detail: actuatorLabel(actuator.label),
      life: 2200,
    });
  } catch (error) {
    toast.add({
      severity: 'error',
      summary: 'キャプチャ保存に失敗しました',
      detail: error instanceof Error ? error.message : '予期しないエラーです。',
      life: 3200,
    });
  }
}

async function handleStartTelemetryRecording(scope: 'all' | 'selected', actuatorId?: number): Promise<void> {
  try {
    await store.beginTelemetryRecording(scope, actuatorId);
    toast.add({
      severity: 'success',
      summary: '記録を開始しました',
      detail:
        scope === 'selected'
          ? `${store.telemetryRecording.current_log_name ?? 'telemetry.csv'} / 選択軸`
          : `${store.telemetryRecording.current_log_name ?? 'telemetry.csv'} / 全8軸`,
      life: 2000,
    });
  } catch (error) {
    toast.add({
      severity: 'error',
      summary: '記録開始に失敗しました',
      detail: error instanceof Error ? error.message : '予期しないエラーです。',
      life: 3200,
    });
  }
}

async function handleStopTelemetryRecording(): Promise<void> {
  try {
    const lastLogName = store.telemetryRecording.current_log_name ?? store.telemetryRecording.latest_log_name;
    await store.endTelemetryRecording();
    toast.add({
      severity: 'success',
      summary: '記録を停止しました',
      detail: lastLogName ?? '最新ログを保存しました。',
      life: 2000,
    });
  } catch (error) {
    toast.add({
      severity: 'error',
      summary: '記録停止に失敗しました',
      detail: error instanceof Error ? error.message : '予期しないエラーです。',
      life: 3200,
    });
  }
}

function handleDownloadTelemetryRecording(): void {
  store.downloadLatestTelemetryRecording();
}

async function handleCalibrateImuLevel(): Promise<void> {
  sensorCalibrationBusy.value = true;
  try {
    await store.setImuLevelCalibration();
    toast.add({
      severity: 'success',
      summary: 'IMU水平補正を保存しました',
      detail: '現在のRoll/Pitchを0度基準にしました。',
      life: 2200,
    });
  } catch (error) {
    toast.add({
      severity: 'error',
      summary: 'IMU水平補正に失敗しました',
      detail: error instanceof Error ? error.message : '不明なエラーです。',
      life: 3200,
    });
  } finally {
    sensorCalibrationBusy.value = false;
  }
}

async function handleCalibrateImuGyro(): Promise<void> {
  sensorCalibrationBusy.value = true;
  try {
    await store.setImuGyroZeroCalibration();
    toast.add({
      severity: 'success',
      summary: 'IMUジャイロゼロを保存しました',
      detail: '静止時のジャイロ平均値をオフセットにしました。',
      life: 2200,
    });
  } catch (error) {
    toast.add({
      severity: 'error',
      summary: 'IMUジャイロ補正に失敗しました',
      detail: error instanceof Error ? error.message : '不明なエラーです。',
      life: 3200,
    });
  } finally {
    sensorCalibrationBusy.value = false;
  }
}

async function handleResetImuCalibration(): Promise<void> {
  sensorCalibrationBusy.value = true;
  try {
    await store.clearImuCalibration();
    toast.add({
      severity: 'success',
      summary: 'IMU補正をリセットしました',
      detail: '水平補正とジャイロオフセットを初期値に戻しました。',
      life: 2200,
    });
  } catch (error) {
    toast.add({
      severity: 'error',
      summary: 'IMU補正リセットに失敗しました',
      detail: error instanceof Error ? error.message : '不明なエラーです。',
      life: 3200,
    });
  } finally {
    sensorCalibrationBusy.value = false;
  }
}

async function handleStartMagCalibration(): Promise<void> {
  magCalibrationBusy.value = true;
  magCalibrationQuality.value = null;
  try {
    await store.startMagCalibration();
    toast.add({
      severity: 'info',
      summary: '磁気較正を開始しました',
      detail: 'ロボットを全方向にゆっくり回転させてください。',
      life: 3000,
    });
  } catch (error) {
    toast.add({
      severity: 'error',
      summary: '磁気較正の開始に失敗しました',
      detail: error instanceof Error ? error.message : '不明なエラーです。',
      life: 3200,
    });
  } finally {
    magCalibrationBusy.value = false;
  }
}

async function handleFinishMagCalibration(): Promise<void> {
  magCalibrationBusy.value = true;
  try {
    const quality = await store.finishMagCalibration();
    magCalibrationQuality.value = quality;
    toast.add({
      severity: 'success',
      summary: '磁気較正を保存しました',
      detail: `サンプル数 ${quality.sample_count} / 残差 ${quality.residual.toFixed(3)} / カバレッジ ${(quality.coverage * 100).toFixed(0)}%`,
      life: 4000,
    });
  } catch (error) {
    toast.add({
      severity: 'error',
      summary: '磁気較正に失敗しました',
      detail: error instanceof Error ? error.message : '不明なエラーです。回転範囲を広げて再試行してください。',
      life: 4000,
    });
  } finally {
    magCalibrationBusy.value = false;
  }
}

async function handleCancelMagCalibration(): Promise<void> {
  magCalibrationBusy.value = true;
  try {
    await store.cancelMagCalibration();
    toast.add({
      severity: 'info',
      summary: '磁気較正をキャンセルしました',
      life: 2000,
    });
  } catch (error) {
    toast.add({
      severity: 'error',
      summary: 'キャンセルに失敗しました',
      detail: error instanceof Error ? error.message : '不明なエラーです。',
      life: 3200,
    });
  } finally {
    magCalibrationBusy.value = false;
  }
}

async function handleToggleStabilization(enabled: boolean): Promise<void> {
  stabilizationToggleBusy.value = true;
  try {
    await store.setStabilizationEnabled(enabled);
    toast.add({
      severity: enabled ? 'warn' : 'success',
      summary: enabled ? 'スタビライゼーションを有効化しました' : 'スタビライゼーションを無効化しました',
      life: 2200,
    });
  } catch (error) {
    toast.add({
      severity: 'error',
      summary: 'スタビライゼーション設定に失敗しました',
      detail: error instanceof Error ? error.message : '不明なエラーです。',
      life: 3200,
    });
  } finally {
    stabilizationToggleBusy.value = false;
  }
}

async function handleApplyStabilizationGains(gains: StabilizationGains): Promise<void> {
  stabilizationGainsBusy.value = true;
  stabilizationGainsError.value = false;
  try {
    await store.applyStabilizationGains(gains);
    toast.add({
      severity: 'success',
      summary: 'ゲインを適用しました',
      life: 2000,
    });
  } catch (error) {
    // Signals StabilizationPanel to keep the operator's edited (unsaved)
    // values instead of resyncing the form from the pre-edit server state.
    stabilizationGainsError.value = true;
    toast.add({
      severity: 'error',
      summary: 'ゲイン適用に失敗しました',
      detail: error instanceof Error ? error.message : '不明なエラーです。',
      life: 3200,
    });
  } finally {
    stabilizationGainsBusy.value = false;
  }
}

async function handleLibraryMotion(category: MotionCategory, name: string): Promise<void> {
  try {
    const detail = await store.loadMotionFile(category, name);
    const intervalSec = detail.item.interval_sec ?? 1 / 30;
    await store.startPlayback(detail.rows, intervalSec, detail.item.loop, {
      motionName: detail.item.name,
      motionCategory: detail.item.category,
      advanceMode: detail.item.advance_mode,
      positionTolerance: detail.item.position_tolerance,
      pressureThreshold: detail.item.pressure_threshold,
      stepTimeoutSec: detail.item.step_timeout_sec,
      settleTimeSec: detail.item.settle_time_sec,
    });
    toast.add({
      severity: 'success',
      summary: 'モーション開始',
      detail: `${category === 'fixed' ? 'Fixed' : 'Custom'} / ${name}`,
      life: 1600,
    });
  } catch (error) {
    toast.add({
      severity: 'error',
      summary: 'モーション開始に失敗しました',
      detail: error instanceof Error ? error.message : '不明なエラーです。',
      life: 3200,
    });
  }
}

async function handleFixedMotion(motion: FixedMotion): Promise<void> {
  await handleLibraryMotion('fixed', motion);
}

async function handleStopMotion(): Promise<void> {
  try {
    await store.stopPlayback();
    toast.add({
      severity: 'success',
      summary: '停止しました',
      detail: '再生中のモーションを停止しました。',
      life: 1600,
    });
  } catch (error) {
    toast.add({
      severity: 'error',
      summary: '停止に失敗しました',
      detail: error instanceof Error ? error.message : '不明なエラーです。',
      life: 3200,
    });
  }
}

async function startHomeMotion(): Promise<void> {
  homeBusy.value = true;
  try {
    await store.moveHome(true);
    toast.add({
      severity: 'info',
      summary: 'Homeへ移動中',
      detail: '全軸を最大150 unit/sで中立姿勢へ移動しています。',
      life: 3000,
    });
  } catch (error) {
    toast.add({
      severity: 'error',
      summary: 'Homeを開始できません',
      detail: error instanceof Error ? error.message : '接続状態を確認してください。',
      life: 3500,
    });
  } finally {
    homeBusy.value = false;
  }
}

function handleHomeMotion(): void {
  confirm.require({
    header: 'Home姿勢へ移動',
    message:
      '全脚を安定姿勢へゆっくり移動します。支持治具、周囲の安全、非常停止手段を確認しましたか？\n途中停止はヘッダのStopを押してください。',
    icon: 'pi pi-exclamation-triangle',
    modal: true,
    blockScroll: true,
    acceptLabel: 'Homeへ移動',
    rejectLabel: 'キャンセル',
    defaultFocus: 'reject',
    acceptProps: { severity: 'info', icon: 'pi pi-home' },
    rejectProps: { severity: 'secondary', outlined: true },
    accept: () => void startHomeMotion(),
  });
}

onMounted(async () => {
  syncViewportMode();
  window.addEventListener('resize', syncViewportMode);
  try {
    await store.initialize();
  } catch (error) {
    toast.add({
      severity: 'error',
      summary: '初期化に失敗しました',
      detail: error instanceof Error ? error.message : '不明なエラーです。',
      life: 4000,
    });
  }
});

onBeforeUnmount(() => {
  window.removeEventListener('resize', syncViewportMode);
  store.dispose();
});
</script>

<template>
  <Toast position="top-right" />
  <ConfirmDialog />

  <div class="console-shell" :class="{ 'is-mobile-shell': isMobile }">
    <StatusToolbar
      :system="store.system"
      :ws-state="store.wsState"
      :loading="store.loading"
      :home-busy="homeBusy"
      :motion-library="store.motionLibrary"
      @refresh="refreshSnapshot"
      @toggle-nav="navOpen = true"
      @fixed-motion="handleFixedMotion"
      @play-library-motion="handleLibraryMotion"
      @stop-motion="handleStopMotion"
      @home="handleHomeMotion"
    />

    <HardwareNotice :hardware="store.hardware" />

    <Drawer v-model:visible="navOpen" header="Quick Guide" position="left" class="app-drawer">
      <div class="drawer-content">
        <p class="drawer-lead">Dashboard で一覧、制御、3D の確認をまとめて行えます。</p>
        <ul class="drawer-list">
          <li>左: アクチュエータ一覧から確認対象を選択</li>
          <li>中央: スライダやゲインで位置とコマンドを即時操作</li>
          <li>右: 3D 表示で脚の姿勢を確認</li>
        </ul>
      </div>
    </Drawer>

    <main class="console-main">
      <Tabs v-model:value="store.activeTab" class="h-full flex flex-col">
        <TabList>
          <Tab v-for="tab in tabOptions" :key="tab.value" :value="tab.value">{{ tab.label }}</Tab>
        </TabList>

        <TabPanels class="flex-1 overflow-hidden flex flex-col p-0">
          <TabPanel value="dashboard" class="flex-1 overflow-hidden">
            <section class="cockpit-layout h-full">
              <div class="cockpit-main-grid cockpit-main-grid-wide h-full overflow-hidden" :class="{ 'is-mobile-grid': isMobile }">
                <div class="cockpit-side-stack h-full overflow-hidden flex flex-col">
                  <ActuatorTable
                    :actuators="store.actuators"
                    :histories="store.actuatorHistories"
                    :loading="store.loading"
                    :selected-actuator-id="store.selectedActuatorId"
                    scroll-height="flex"
                    class="flex-1 min-h-0"
                    @select="store.selectActuator($event.actuator_id)"
                  />
                </div>

                <ActuatorControlPanel
                  :actuator="store.selectedActuator"
                  :samples="store.selectedActuatorHistory"
                  :busy="store.loading"
                  compact
                  :telemetry-recording="store.telemetryRecording.is_recording"
                  :telemetry-log-name="store.telemetryRecording.current_log_name ?? store.telemetryRecording.latest_log_name"
                  :telemetry-recording-scope="store.telemetryRecording.scope"
                  :telemetry-recording-actuator-id="store.telemetryRecording.actuator_id"
                  @target="handleTarget"
                  @save-gain="handleGain"
                  @save-capture="handleSaveCapture"
                  @reload-gain="handleReloadGain"
                  @capture="handleCapture"
                  @start-recording="handleStartTelemetryRecording"
                  @stop-recording="handleStopTelemetryRecording"
                  @download-recording="handleDownloadTelemetryRecording"
                />

                <FocusedLegView
                  v-if="store.activeTab === 'dashboard'"
                  :focused-leg-id="store.focusedLegId"
                  :legs="store.legs"
                  :supporting-leg-ids="store.supportingLegIds"
                  :imu-quaternion="store.sensors?.imu.quaternion ?? null"
                  :imu-orientation="store.sensors?.imu.orientation ?? null"
                  compact
                  @update:focused-leg-id="store.selectLeg"
                />
              </div>
            </section>
          </TabPanel>

          <TabPanel value="legs" class="flex-1 overflow-auto">
            <section class="tab-layout">
              <FocusedLegView
                v-if="store.activeTab === 'legs'"
                :focused-leg-id="store.focusedLegId"
                :legs="store.legs"
                :supporting-leg-ids="store.supportingLegIds"
                :imu-quaternion="store.sensors?.imu.quaternion ?? null"
                :imu-orientation="store.sensors?.imu.orientation ?? null"
                @update:focused-leg-id="store.selectLeg"
              />
            </section>
          </TabPanel>

          <TabPanel value="motion" class="flex-1 overflow-auto">
            <section v-if="store.activeTab === 'motion'" class="motion-extras standing-section">
              <StandingPanel />
            </section>
            <MotionControlPanel />
            <section v-if="store.activeTab === 'motion'" class="motion-extras">
              <GaitDiagramPanel />
              <ExperimentPanel />
            </section>
          </TabPanel>

          <TabPanel value="contacts" class="flex-1 overflow-auto">
            <section class="tab-layout">
              <ContactSensorPanel
                v-if="store.activeTab === 'contacts'"
                :focused-leg-id="store.focusedLegId"
                :legs="store.legs"
                :sensors="store.sensors"
                :states="store.contactLegStates"
                :calibration="store.contactCalibration"
                :imu-quaternion="store.sensors?.imu.quaternion ?? null"
                :imu-orientation="store.sensors?.imu.orientation ?? null"
                @update:focused-leg-id="store.selectLeg"
                @save="(calibration) => store.saveContactCalibration(calibration)"
              />
            </section>
          </TabPanel>

          <TabPanel value="controller" class="flex-1 overflow-hidden">
            <section class="tab-layout">
              <GamepadPanel v-if="store.activeTab === 'controller'" />
            </section>
          </TabPanel>

          <TabPanel value="sensors" class="flex-1 overflow-auto">
            <section class="tab-layout">
              <SensorCalibrationPanel
                :sensors="store.sensors"
                :busy="sensorCalibrationBusy"
                :mag-calibration-busy="magCalibrationBusy"
                :mag-calibration-quality="magCalibrationQuality"
                @calibrate-level="handleCalibrateImuLevel"
                @calibrate-gyro="handleCalibrateImuGyro"
                @reset-calibration="handleResetImuCalibration"
                @start-mag-calibration="handleStartMagCalibration"
                @finish-mag-calibration="handleFinishMagCalibration"
                @cancel-mag-calibration="handleCancelMagCalibration"
              />
              <StabilizationPanel
                :stabilization="store.stabilization"
                :toggle-busy="stabilizationToggleBusy"
                :gains-busy="stabilizationGainsBusy"
                :gains-error="stabilizationGainsError"
                @toggle-enabled="handleToggleStabilization"
                @apply-gains="handleApplyStabilizationGains"
              />
            </section>
          </TabPanel>
        </TabPanels>
      </Tabs>
    </main>
  </div>
</template>
