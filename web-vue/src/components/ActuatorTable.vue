<script setup lang="ts">
import Column from 'primevue/column';
import DataTable from 'primevue/datatable';

import ActuatorMiniTrend from '@/components/ActuatorMiniTrend.vue';
import type { ActuatorState, TelemetrySample } from '@/types/control';
import { actuatorLabel } from '@/utils/i18n';

defineProps<{
  actuators: ActuatorState[];
  histories: Record<number, TelemetrySample[]>;
  loading: boolean;
  scrollHeight?: string;
  selectedActuatorId?: number;
}>();

defineEmits<{
  select: [actuator: ActuatorState];
}>();
</script>

<template>
  <DataTable
    :value="actuators"
    data-key="actuator_id"
    responsive-layout="scroll"
    :loading="loading"
    class="actuator-table"
    :scrollable="Boolean(scrollHeight)"
    :scroll-height="scrollHeight"
    selection-mode="single"
    :selection="actuators.find((item) => item.actuator_id === selectedActuatorId) ?? null"
    @row-click="$emit('select', $event.data)"
  >
    <Column field="actuator_id" header="ID" />
    <Column header="軸">
      <template #body="{ data }">
        {{ actuatorLabel(data.label) }}
      </template>
    </Column>
    <Column header="ライブ">
      <template #body="{ data }">
        <ActuatorMiniTrend :samples="histories[data.actuator_id] ?? []" />
      </template>
    </Column>
  </DataTable>
</template>
