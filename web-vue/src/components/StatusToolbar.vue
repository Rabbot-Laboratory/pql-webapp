<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue';

import Button from 'primevue/button';
import Menu from 'primevue/menu';
import Popover from 'primevue/popover';
import Toolbar from 'primevue/toolbar';
import { useConfirm } from 'primevue/useconfirm';
import { useToast } from 'primevue/usetoast';

import { fetchSystemInfo, requestSystemPower } from '@/services/controlApi';
import type {
  FixedMotion,
  MotionCategory,
  MotionLibrarySnapshot,
  NetworkInterfaceAddress,
  SystemPowerAction,
  SystemStatus,
} from '@/types/control';

const props = defineProps<{
  system: SystemStatus | null;
  wsState: 'connecting' | 'live' | 'disconnected' | 'error';
  loading: boolean;
  homeBusy: boolean;
  motionLibrary: MotionLibrarySnapshot;
}>();

const emit = defineEmits<{
  refresh: [];
  toggleNav: [];
  fixedMotion: [motion: FixedMotion];
  playLibraryMotion: [category: MotionCategory, name: string];
  stopMotion: [];
  home: [];
}>();

const isFullscreen = ref(false);
const selectedLibraryKey = ref('');
const networkInterfaces = ref<NetworkInterfaceAddress[]>([]);
const networkPopover = ref<InstanceType<typeof Popover> | null>(null);
const powerMenu = ref<InstanceType<typeof Menu> | null>(null);
const powerBusy = ref(false);
const confirm = useConfirm();
const toast = useToast();

const powerMenuItems = [
  { label: '再起動', icon: 'pi pi-refresh', command: () => confirmPowerAction('reboot') },
  { label: 'シャットダウン', icon: 'pi pi-power-off', command: () => confirmPowerAction('shutdown') },
];

const networkSummary = computed(() =>
  networkInterfaces.value
    .map((item) => `${item.kind === 'wifi' ? 'Wi-Fi' : 'LAN'}: ${item.address ?? '未接続'}`)
    .join(' / '),
);

const playbackRunning = computed(() => props.system?.playback_status === 'running');
const currentMotionLabel = computed(() => {
  if (!props.system?.current_motion_name) {
    return '';
  }
  const prefix = props.system.current_motion_category === 'fixed' ? 'Fixed' : 'Custom';
  return `${prefix} / ${props.system.current_motion_name}`;
});

const loopMotionLabel = computed(() => {
  if (props.system?.playback_status !== 'running' || !props.system.current_motion_loop) {
    return '';
  }
  return 'Loop';
});

const libraryOptions = computed(() => [
  ...props.motionLibrary.fixed.map((item) => ({
    key: `fixed:${item.name}`,
    label: `Fixed / ${item.name}`,
    category: 'fixed' as MotionCategory,
    name: item.name,
  })),
  ...props.motionLibrary.custom.map((item) => ({
    key: `custom:${item.name}`,
    label: `Custom / ${item.name}`,
    category: 'custom' as MotionCategory,
    name: item.name,
  })),
]);

const statusIcons = computed(() => [
  {
    icon: 'pi pi-link',
    state:
      props.system?.connection_state === 'connected'
        ? 'ok'
        : props.system?.connection_state === 'connecting'
          ? 'warn'
          : props.system?.connection_state === 'error'
            ? 'danger'
            : 'muted',
    title: `ESP 接続: ${props.system?.connection_state ?? 'unknown'}`,
  },
  {
    icon: 'pi pi-wifi',
    state:
      props.wsState === 'live'
        ? 'ok'
        : props.wsState === 'connecting'
          ? 'warn'
          : props.wsState === 'error'
            ? 'danger'
            : 'muted',
    title: `WebSocket: ${props.wsState}`,
  },
  {
    icon: 'pi pi-play-circle',
    state:
      props.system?.playback_status === 'running'
        ? 'ok'
        : props.system?.playback_status === 'stopping'
          ? 'warn'
          : 'muted',
    title: `再生状態: ${props.system?.playback_status ?? 'idle'}`,
  },
  {
    icon: 'pi pi-desktop',
    state: props.system?.emulate_devices ? 'warn' : 'muted',
    title: props.system?.emulate_devices ? 'デモモード' : '実機モード',
  },
]);

watch(
  libraryOptions,
  (options) => {
    if (!options.length) {
      selectedLibraryKey.value = '';
      return;
    }
    if (!options.find((option) => option.key === selectedLibraryKey.value)) {
      selectedLibraryKey.value = options[0].key;
    }
  },
  { immediate: true },
);

watch(
  () => [props.system?.current_motion_name, props.system?.current_motion_category, props.system?.playback_status] as const,
  () => {
    if (props.system?.playback_status !== 'running' || !props.system.current_motion_name || !props.system.current_motion_category) {
      return;
    }
    const expectedKey = `${props.system.current_motion_category}:${props.system.current_motion_name}`;
    if (libraryOptions.value.some((option) => option.key === expectedKey)) {
      selectedLibraryKey.value = expectedKey;
    }
  },
  { immediate: true },
);

function handleFullscreenChange(): void {
  isFullscreen.value = !!document.fullscreenElement;
}

function toggleFullscreen(): void {
  if (!document.fullscreenElement) {
    document.documentElement.requestFullscreen().catch((error) => {
      console.warn('fullscreen failed', error);
    });
    return;
  }
  document.exitFullscreen().catch((error) => {
    console.warn('exit fullscreen failed', error);
  });
}

async function loadSystemInfo(): Promise<void> {
  try {
    networkInterfaces.value = (await fetchSystemInfo()).network_interfaces;
  } catch (error) {
    console.warn('system info fetch failed', error);
  }
}

async function copyAddress(address: string | null): Promise<void> {
  if (!address) return;
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(address);
    } else {
      const input = document.createElement('textarea');
      input.value = address;
      input.style.position = 'fixed';
      input.style.opacity = '0';
      document.body.appendChild(input);
      input.select();
      const copied = document.execCommand('copy');
      input.remove();
      if (!copied) throw new Error('copy failed');
    }
    toast.add({ severity: 'success', summary: 'IPアドレスをコピーしました', detail: address, life: 1600 });
  } catch {
    toast.add({ severity: 'error', summary: 'コピーできませんでした', detail: address, life: 2500 });
  }
}

async function runPowerAction(action: SystemPowerAction): Promise<void> {
  powerBusy.value = true;
  try {
    await requestSystemPower(action);
    toast.add({
      severity: 'info',
      summary: action === 'reboot' ? '再起動します' : 'シャットダウンします',
      detail: 'まもなく接続が切れます。',
      life: 2500,
    });
  } catch (error) {
    toast.add({
      severity: 'error',
      summary: '電源操作に失敗しました',
      detail: error instanceof Error ? error.message : '不明なエラーです。',
      life: 3500,
    });
  } finally {
    powerBusy.value = false;
  }
}

function confirmPowerAction(action: SystemPowerAction): void {
  const reboot = action === 'reboot';
  confirm.require({
    header: reboot ? 'Raspberry Piを再起動' : 'Raspberry Piをシャットダウン',
    message: `ロボットを支持し、空圧を遮断してください。${reboot ? '再起動' : 'シャットダウン'}を実行しますか？`,
    icon: 'pi pi-exclamation-triangle',
    acceptLabel: reboot ? '再起動する' : 'シャットダウンする',
    rejectLabel: 'キャンセル',
    defaultFocus: 'reject',
    acceptProps: { severity: 'danger', icon: reboot ? 'pi pi-refresh' : 'pi pi-power-off' },
    rejectProps: { severity: 'secondary', outlined: true },
    accept: () => void runPowerAction(action),
  });
}

function togglePowerMenu(event: Event): void {
  powerMenu.value?.toggle(event);
}

function toggleNetworkPopover(event: Event): void {
  networkPopover.value?.toggle(event);
}

function refreshAll(): void {
  emit('refresh');
  void loadSystemInfo();
}

function playSelectedLibraryMotion(): void {
  const selected = libraryOptions.value.find((option) => option.key === selectedLibraryKey.value);
  if (!selected) {
    return;
  }
  emit('playLibraryMotion', selected.category, selected.name);
}

onMounted(() => {
  document.addEventListener('fullscreenchange', handleFullscreenChange);
  void loadSystemInfo();
});

onUnmounted(() => {
  document.removeEventListener('fullscreenchange', handleFullscreenChange);
});
</script>

<template>
  <Toolbar class="top-toolbar compact-toolbar motion-toolbar">
    <template #start>
      <div class="toolbar-brand compact-brand">
        <Button icon="pi pi-bars" text rounded aria-label="ナビゲーションを開く" @click="$emit('toggleNav')" />
        <img src="@/assets/logo.png" class="brand-logo" alt="RABBOT LABORATORY" />
      </div>
    </template>

    <template #center>
      <div class="motion-toolbar-center" role="group" aria-label="モーション操作">
        <Button
          label="Home"
          icon="pi pi-home"
          size="small"
          rounded
          severity="info"
          :loading="homeBusy"
          :disabled="homeBusy || loading || wsState !== 'live' || system?.connection_state !== 'connected' || playbackRunning"
          title="全脚をhome.csvの安定姿勢へゆっくり移動"
          @click="$emit('home')"
        />
        <Button label="Crawl" size="small" text rounded @click="$emit('fixedMotion', 'crawl')" />
        <Button label="Trot" size="small" text rounded @click="$emit('fixedMotion', 'trot')" />
        <Button label="Pace" size="small" text rounded @click="$emit('fixedMotion', 'pace')" />
        <Button label="Bound" size="small" text rounded @click="$emit('fixedMotion', 'bound')" />

        <div class="toolbar-motion-select-wrap">
          <select v-model="selectedLibraryKey" class="toolbar-motion-select" aria-label="モーション選択">
            <option v-for="option in libraryOptions" :key="option.key" :value="option.key">
              {{ option.label }}
            </option>
          </select>
        </div>

        <Button
          label="Play"
          size="small"
          rounded
          severity="secondary"
          :disabled="!selectedLibraryKey"
          @click="playSelectedLibraryMotion"
        />
        <Button
          label="Stop"
          size="small"
          rounded
          severity="danger"
          :outlined="!playbackRunning"
          @click="$emit('stopMotion')"
        />
        <span
          v-if="loopMotionLabel"
          class="toolbar-loop-badge"
          :title="`${currentMotionLabel || '現在のモーション'} はループ再生中`"
        >
          <i class="pi pi-refresh"></i>
          {{ loopMotionLabel }}
        </span>
        <span v-if="currentMotionLabel" class="toolbar-current-motion" :title="currentMotionLabel">
          {{ currentMotionLabel }}
        </span>
      </div>
    </template>

    <template #end>
      <div class="toolbar-actions compact-actions">
        <Popover ref="networkPopover" class="toolbar-network-popover">
          <div class="toolbar-network-addresses" aria-label="Raspberry PiのIPアドレス">
            <span
              v-for="item in networkInterfaces"
              :key="`${item.interface}:${item.address}`"
              class="toolbar-network-address"
              :title="`${item.interface} (${item.kind === 'wifi' ? 'Wi-Fi' : '有線LAN'})`"
            >
              <i :class="item.kind === 'wifi' ? 'pi pi-wifi' : 'pi pi-server'"></i>
              <span>{{ item.kind === 'wifi' ? 'Wi-Fi' : 'LAN' }}</span>
              <code>{{ item.address ?? '未接続' }}</code>
              <Button
                icon="pi pi-copy"
                text
                rounded
                size="small"
                :disabled="!item.address"
                :aria-label="item.address ? `${item.address}をコピー` : `${item.interface}は未接続`"
                @click="copyAddress(item.address)"
              />
            </span>
          </div>
        </Popover>
        <Button
          icon="pi pi-sitemap"
          text
          rounded
          aria-label="Raspberry PiのIPアドレスを表示"
          :title="networkSummary || 'IPアドレスを表示'"
          severity="secondary"
          @click="toggleNetworkPopover"
        />
        <div class="toolbar-status-icons" aria-label="システム状態">
          <span
            v-for="status in statusIcons"
            :key="status.icon"
            class="toolbar-status-icon"
            :class="`is-${status.state}`"
            :title="status.title"
          >
            <i :class="status.icon"></i>
          </span>
        </div>
        <Button
          :icon="isFullscreen ? 'pi pi-window-minimize' : 'pi pi-window-maximize'"
          text
          rounded
          aria-label="全画面表示切替"
          severity="secondary"
          @click="toggleFullscreen"
        />
        <Button
          :loading="loading"
          icon="pi pi-refresh"
          text
          rounded
          aria-label="更新"
          severity="secondary"
          @click="refreshAll"
        />
        <Menu ref="powerMenu" :model="powerMenuItems" popup />
        <Button
          :loading="powerBusy"
          icon="pi pi-power-off"
          text
          rounded
          aria-label="Raspberry Piの電源操作"
          severity="danger"
          @click="togglePowerMenu"
        />
      </div>
    </template>
  </Toolbar>
</template>
