<script setup lang="ts">
import Card from 'primevue/card';
import Tag from 'primevue/tag';

import type { SystemStatus } from '@/types/control';
import { connectionStateLabel, emulationLabel, playbackStatusLabel } from '@/utils/i18n';

defineProps<{
  system: SystemStatus | null;
  actuatorCount: number;
}>();
</script>

<template>
  <div class="overview-grid overview-grid-compact">
    <Card class="overview-card">
      <template #title>接続状態</template>
      <template #content>
        <div class="overview-value">
          <strong>{{ connectionStateLabel(system?.connection_state) }}</strong>
          <Tag :severity="system?.connection_state === 'connected' ? 'success' : 'warn'" :value="emulationLabel(system?.emulate_devices)" />
        </div>
      </template>
    </Card>
    <Card class="overview-card">
      <template #title>再生状態</template>
      <template #content>
        <div class="overview-value">
          <strong>{{ playbackStatusLabel(system?.playback_status) }}</strong>
          <span>CSV 再生やティーチング状態をここに集約します。</span>
        </div>
      </template>
    </Card>
    <Card class="overview-card">
      <template #title>監視中の軸</template>
      <template #content>
        <div class="overview-value">
          <strong>{{ actuatorCount }}</strong>
          <span>4 脚の hip / knee をまとめて監視中</span>
        </div>
      </template>
    </Card>
  </div>
</template>
