<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue';

import Button from 'primevue/button';
import Toolbar from 'primevue/toolbar';

import type { FixedMotion, MotionCategory, MotionLibrarySnapshot, SystemStatus } from '@/types/control';

const props = defineProps<{
  system: SystemStatus | null;
  wsState: 'connecting' | 'live' | 'disconnected' | 'error';
  loading: boolean;
  motionLibrary: MotionLibrarySnapshot;
}>();

const emit = defineEmits<{
  refresh: [];
  toggleNav: [];
  fixedMotion: [motion: FixedMotion];
  playLibraryMotion: [category: MotionCategory, name: string];
  stopMotion: [];
}>();

const isFullscreen = ref(false);
const selectedLibraryKey = ref('');

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

function playSelectedLibraryMotion(): void {
  const selected = libraryOptions.value.find((option) => option.key === selectedLibraryKey.value);
  if (!selected) {
    return;
  }
  emit('playLibraryMotion', selected.category, selected.name);
}

onMounted(() => {
  document.addEventListener('fullscreenchange', handleFullscreenChange);
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
          @click="$emit('refresh')"
        />
      </div>
    </template>
  </Toolbar>
</template>
