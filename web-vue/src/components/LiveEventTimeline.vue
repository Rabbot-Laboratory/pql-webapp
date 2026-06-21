<script setup lang="ts">
import Card from 'primevue/card';
import Timeline from 'primevue/timeline';

import type { UiEventItem } from '@/types/control';
import { eventTypeLabel } from '@/utils/i18n';

defineProps<{
  events: UiEventItem[];
}>();

function formatTimestamp(value: string): string {
  return new Date(value).toLocaleTimeString();
}
</script>

<template>
  <Card class="timeline-card">
    <template #title>イベントログ</template>
    <template #content>
      <Timeline :value="events" align="left" class="event-timeline">
        <template #content="{ item }">
          <div class="event-item">
            <strong>{{ eventTypeLabel(item.type) }}</strong>
            <p>{{ item.message }}</p>
            <small>{{ formatTimestamp(item.timestamp) }}</small>
          </div>
        </template>
      </Timeline>
    </template>
  </Card>
</template>
