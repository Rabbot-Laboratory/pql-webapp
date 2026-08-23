<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue';

import Button from 'primevue/button';
import Tag from 'primevue/tag';
import Toolbar from 'primevue/toolbar';

import type { SystemStatus } from '@/types/control';

const props = defineProps<{
  system: SystemStatus | null;
  wsState: 'connecting' | 'live' | 'disconnected' | 'error';
  loading: boolean;
}>();

defineEmits<{
  refresh: [];
}>();

const isFullscreen = ref(false);
const espReady = computed(() =>
  ['connected', 'emulated'].includes(props.system?.connection_state ?? ''),
);
const statusIcons = computed(() => [
  {
    icon: 'pi pi-link',
    state: espReady.value ? 'ok' : 'warn',
    title: `ESP32: ${props.system?.connection_state ?? 'connecting'}`,
  },
  {
    icon: 'pi pi-wifi',
    state: props.wsState === 'live' ? 'ok' : props.wsState === 'error' ? 'danger' : 'warn',
    title: `WebSocket: ${props.wsState}`,
  },
  {
    icon: 'pi pi-desktop',
    state: props.system?.emulate_devices ? 'warn' : 'muted',
    title: props.system?.emulate_devices ? 'デモモード' : '実機モード',
  },
]);

function handleFullscreenChange(): void {
  isFullscreen.value = Boolean(document.fullscreenElement);
}

function toggleFullscreen(): void {
  if (document.fullscreenElement) {
    void document.exitFullscreen();
  } else {
    void document.documentElement.requestFullscreen();
  }
}

onMounted(() => document.addEventListener('fullscreenchange', handleFullscreenChange));
onUnmounted(() => document.removeEventListener('fullscreenchange', handleFullscreenChange));
</script>

<template>
  <Toolbar class="top-toolbar compact-toolbar motion-toolbar">
    <template #start>
      <div class="toolbar-brand compact-brand">
        <img src="@/assets/logo.png" class="brand-logo" alt="RABBOT LABORATORY" />
        <div>
          <p class="toolbar-kicker">Single Leg</p>
          <h1>Control Console</h1>
        </div>
      </div>
    </template>

    <template #center>
      <div class="motion-toolbar-center" aria-label="1脚構成">
        <Tag severity="contrast" value="HIP / CH 0" />
        <Tag severity="info" value="KNEE / CH 1" />
        <Tag severity="secondary" value="ESP32 × 1" />
        <Tag v-if="system?.emulate_devices" severity="warn" value="DEMO" />
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
          severity="secondary"
          aria-label="全画面表示切替"
          @click="toggleFullscreen"
        />
        <Button
          :loading="loading"
          icon="pi pi-refresh"
          text
          rounded
          severity="secondary"
          aria-label="更新"
          @click="$emit('refresh')"
        />
      </div>
    </template>
  </Toolbar>
</template>

