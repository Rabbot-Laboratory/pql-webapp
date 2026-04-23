<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';

import Drawer from 'primevue/drawer';
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
import MotionControlPanel from '@/components/MotionControlPanel.vue';
import PressureMonitorPanel from '@/components/PressureMonitorPanel.vue';
import StatusToolbar from '@/components/StatusToolbar.vue';
import { useControlStore } from '@/stores/control';
import type { ActuatorState, ControlMode, FixedMotion, MotionCategory } from '@/types/control';
import { actuatorLabel } from '@/utils/i18n';

const store = useControlStore();
const toast = useToast();
const navOpen = ref(false);
const isMobile = ref(false);

const tabOptions = computed(() =>
  isMobile.value
    ? [
        { value: 'dashboard', label: '操作' },
        { value: 'legs', label: '脚' },
        { value: 'motion', label: '動作' },
        { value: 'pressure', label: '圧力' },
      ]
    : [
        { value: 'dashboard', label: 'Dashboard' },
        { value: 'legs', label: 'Kinematics' },
        { value: 'motion', label: 'Motion' },
        { value: 'pressure', label: 'Pressure' },
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

  <div class="console-shell" :class="{ 'is-mobile-shell': isMobile }">
    <StatusToolbar
      :system="store.system"
      :ws-state="store.wsState"
      :loading="store.loading"
      :motion-library="store.motionLibrary"
      @refresh="refreshSnapshot"
      @toggle-nav="navOpen = true"
      @fixed-motion="handleFixedMotion"
      @play-library-motion="handleLibraryMotion"
      @stop-motion="handleStopMotion"
    />

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
                @update:focused-leg-id="store.selectLeg"
              />
            </section>
          </TabPanel>

          <TabPanel value="motion" class="flex-1 overflow-auto">
            <MotionControlPanel />
          </TabPanel>

          <TabPanel value="pressure" class="flex-1 overflow-auto">
            <PressureMonitorPanel :actuators="store.actuators" />
          </TabPanel>
        </TabPanels>
      </Tabs>
    </main>
  </div>
</template>
