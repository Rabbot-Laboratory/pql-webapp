<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';

import Button from 'primevue/button';
import Tag from 'primevue/tag';

import type { HardwareDeviceStatus, HardwareStatus } from '@/types/control';

const props = defineProps<{
  hardware: HardwareStatus | null;
}>();

const notificationReady = ref(false);
const dismissedFingerprint = ref<string | null>(null);
const detailsOpen = ref(false);
let graceTimer: number | null = null;

const issues = computed(() =>
  (props.hardware?.devices ?? []).filter(
    (device) =>
      device.required &&
      device.connection_state !== 'connected' &&
      device.connection_state !== 'emulated',
  ),
);
const fingerprint = computed(() => issues.value.map((device) => device.device_id).sort().join('|'));
const bannerVisible = computed(
  () => notificationReady.value && issues.value.length > 0 && dismissedFingerprint.value !== fingerprint.value,
);
const compactVisible = computed(
  () => notificationReady.value && issues.value.length > 0 && dismissedFingerprint.value === fingerprint.value,
);

watch(fingerprint, (next, previous) => {
  if (!next) {
    dismissedFingerprint.value = null;
    detailsOpen.value = false;
    return;
  }
  // A device that recovered and later disappeared is treated as a new issue.
  if (!previous) {
    dismissedFingerprint.value = null;
  }
});

function dismiss(): void {
  dismissedFingerprint.value = fingerprint.value;
  detailsOpen.value = false;
}

function reopen(): void {
  dismissedFingerprint.value = null;
}

function statusLabel(device: HardwareDeviceStatus): string {
  return {
    disabled: '無効',
    connecting: '接続待ち',
    connected: '接続済み',
    missing: '未接続・再試行中',
    error: 'エラー',
    stale: '更新停止',
    emulated: 'エミュレーション',
  }[device.connection_state];
}

function tagSeverity(device: HardwareDeviceStatus): 'secondary' | 'warn' | 'danger' {
  return device.connection_state === 'error' || device.connection_state === 'stale' ? 'danger' :
    device.connection_state === 'disabled' ? 'secondary' : 'warn';
}

function formatLastSeen(value: string | null): string {
  if (!value) return '受信履歴なし';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('ja-JP');
}

onMounted(() => {
  graceTimer = window.setTimeout(() => {
    notificationReady.value = true;
  }, 2000);
});

onBeforeUnmount(() => {
  if (graceTimer !== null) window.clearTimeout(graceTimer);
});
</script>

<template>
  <section v-if="bannerVisible" class="hardware-notice" aria-live="polite">
    <div class="hardware-notice-main">
      <i class="pi pi-exclamation-triangle" aria-hidden="true" />
      <div>
        <strong>必要な実機デバイスの一部が利用できません</strong>
        <p>
          表示・設定・ログ機能は継続できます。Front/Back USBシリアルはバックグラウンドで再接続します。
        </p>
      </div>
      <span class="hardware-count">{{ issues.length }}</span>
      <Button
        :label="detailsOpen ? '詳細を閉じる' : '詳細'"
        text
        size="small"
        severity="secondary"
        @click="detailsOpen = !detailsOpen"
      />
      <Button
        icon="pi pi-times"
        text
        rounded
        size="small"
        severity="secondary"
        aria-label="ハードウェア通知を閉じる"
        @click="dismiss"
      />
    </div>

    <div v-if="detailsOpen" class="hardware-details">
      <article v-for="device in issues" :key="device.device_id" class="hardware-device-row">
        <div class="hardware-device-heading">
          <strong>{{ device.label }}</strong>
          <Tag :severity="tagSeverity(device)" :value="statusLabel(device)" />
        </div>
        <p>{{ device.detail ?? 'デバイスを確認してください。' }}</p>
        <small>
          {{ device.path ?? 'デバイスパス不明' }} / {{ formatLastSeen(device.last_seen_at) }}
        </small>
      </article>
      <p class="contact-detection-note">
        接地センサはMCP3208との通信状態を表示しています。個別センサの断線判定は実機しきい値確定後に追加します。
      </p>
    </div>
  </section>

  <button
    v-else-if="compactVisible"
    type="button"
    class="hardware-notice-compact"
    title="ハードウェア通知を再表示"
    @click="reopen"
  >
    <i class="pi pi-exclamation-triangle" />
    機器 {{ issues.length }}件
  </button>
</template>

<style scoped>
.hardware-notice {
  margin: .55rem 1rem 0;
  border: 1px solid color-mix(in srgb, var(--p-orange-500) 48%, transparent);
  border-radius: .75rem;
  background: color-mix(in srgb, var(--p-orange-500) 9%, var(--surface-card));
  box-shadow: 0 4px 14px rgba(0, 0, 0, .08);
}
.hardware-notice-main {
  display: flex;
  align-items: center;
  gap: .75rem;
  padding: .7rem .85rem;
}
.hardware-notice-main > .pi { color: var(--p-orange-500); font-size: 1.2rem; }
.hardware-notice-main > div { flex: 1; min-width: 0; }
.hardware-notice-main p { margin: .15rem 0 0; color: var(--text-color-secondary); font-size: .84rem; }
.hardware-count {
  min-width: 1.65rem;
  padding: .15rem .45rem;
  border-radius: 999px;
  background: var(--p-orange-500);
  color: white;
  text-align: center;
  font-size: .78rem;
  font-weight: 700;
}
.hardware-details {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(15rem, 1fr));
  gap: .6rem;
  padding: 0 .85rem .85rem;
}
.hardware-device-row { padding: .7rem; border: 1px solid var(--surface-border); border-radius: .6rem; background: var(--surface-card); }
.hardware-device-heading { display: flex; align-items: center; justify-content: space-between; gap: .5rem; }
.hardware-device-row p { margin: .35rem 0; color: var(--text-color-secondary); font-size: .82rem; overflow-wrap: anywhere; }
.hardware-device-row small { color: var(--text-color-secondary); overflow-wrap: anywhere; }
.contact-detection-note { grid-column: 1 / -1; margin: 0; color: var(--text-color-secondary); font-size: .78rem; }
.hardware-notice-compact {
  position: fixed;
  z-index: 40;
  top: 4.25rem;
  right: 1rem;
  display: flex;
  align-items: center;
  gap: .4rem;
  padding: .42rem .7rem;
  border: 1px solid color-mix(in srgb, var(--p-orange-500) 55%, transparent);
  border-radius: 999px;
  background: var(--surface-card);
  color: var(--p-orange-500);
  box-shadow: 0 4px 12px rgba(0, 0, 0, .16);
  cursor: pointer;
  font: inherit;
  font-size: .8rem;
  font-weight: 700;
}
@media (max-width: 700px) {
  .hardware-notice { margin-inline: .5rem; }
  .hardware-notice-main { align-items: flex-start; flex-wrap: wrap; }
  .hardware-notice-main > div { flex-basis: calc(100% - 3rem); }
  .hardware-notice-main p { display: none; }
  .hardware-notice-compact { top: 3.8rem; right: .5rem; }
}
</style>
