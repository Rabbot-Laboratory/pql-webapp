<script setup lang="ts">
import { computed } from 'vue';

import Card from 'primevue/card';
import SelectButton from 'primevue/selectbutton';
import Splitter from 'primevue/splitter';
import SplitterPanel from 'primevue/splitterpanel';
import Tag from 'primevue/tag';

import RobotModelViewport from '@/components/RobotModelViewport.vue';
import type { LegId, LegPreview } from '@/types/control';
import { legLabel } from '@/utils/i18n';

const props = defineProps<{
  legs: LegPreview[];
  focusedLegId: LegId;
  compact?: boolean;
}>();

const emit = defineEmits<{
  'update:focusedLegId': [legId: LegId];
}>();

const legOptions = computed(() =>
  props.legs.map((leg) => ({
    label: legLabel(leg.leg_id),
    value: leg.leg_id,
  })),
);

const focusedLeg = computed(() => props.legs.find((leg) => leg.leg_id === props.focusedLegId) ?? null);

function degrees(radians: number): string {
  return `${((radians * 180) / Math.PI).toFixed(1)} deg`;
}
</script>

<template>
  <div class="legs-view" :class="{ 'is-compact': compact }">
    <div class="legs-toolbar">
      <div>
        <p class="section-kicker">Kinematics</p>
        <h2>3D Inspector</h2>
      </div>
      <div v-if="!compact" class="legs-toolbar-actions">
        <SelectButton
          :model-value="focusedLegId"
          :options="legOptions"
          option-label="label"
          option-value="value"
          @update:model-value="emit('update:focusedLegId', $event)"
        />
      </div>
      <div v-else-if="focusedLeg" class="legs-toolbar-actions">
        <Tag severity="contrast" :value="legLabel(focusedLeg.leg_id)" />
      </div>
    </div>

    <Card v-if="compact" class="leg-stage-card is-compact">
      <template #content>
        <div class="leg-stage compact-stage">
          <RobotModelViewport :legs="legs" :focused-leg-id="focusedLegId" />
          <div v-if="focusedLeg" class="compact-leg-summary">
            <Tag severity="contrast" :value="`${focusedLeg.hip.joint_name}: ${degrees(focusedLeg.hip.angle_rad)}`" />
            <Tag severity="info" :value="`${focusedLeg.knee.joint_name}: ${degrees(focusedLeg.knee.angle_rad)}`" />
          </div>
        </div>
      </template>
    </Card>

    <Splitter v-else class="legs-splitter" :class="{ 'is-compact': compact }">
      <SplitterPanel :size="62">
        <Card class="leg-stage-card">
          <template #content>
            <div class="leg-stage">
              <div class="leg-stage-header">
                <div>
                  <p class="section-kicker">3D Robot</p>
                  <h3>{{ focusedLeg ? legLabel(focusedLeg.leg_id) : '脚データを読み込み中' }}</h3>
                </div>
              </div>
              <RobotModelViewport :legs="legs" :focused-leg-id="focusedLegId" />
            </div>
          </template>
        </Card>
      </SplitterPanel>

      <SplitterPanel :size="38">
        <div v-if="focusedLeg" class="leg-inspector">
          <Card>
            <template #title>関節対応</template>
            <template #content>
              <div class="info-stack">
                <span>{{ focusedLeg.fixed_joint_name }} は {{ degrees(focusedLeg.fixed_joint_angle_rad) }} で固定</span>
                <span>{{ focusedLeg.hip.joint_name }} に hip を割当</span>
                <span>{{ focusedLeg.knee.joint_name }} に knee を割当</span>
              </div>
            </template>
          </Card>

          <Card>
            <template #title>Hip</template>
            <template #content>
              <div class="info-stack">
                <strong>{{ focusedLeg.hip.position }}</strong>
                <span>{{ degrees(focusedLeg.hip.angle_rad) }}</span>
                <span>目標 {{ focusedLeg.hip.target_position }} / {{ degrees(focusedLeg.hip.target_angle_rad) }}</span>
              </div>
            </template>
          </Card>

          <Card>
            <template #title>Knee</template>
            <template #content>
              <div class="info-stack">
                <strong>{{ focusedLeg.knee.position }}</strong>
                <span>{{ degrees(focusedLeg.knee.angle_rad) }}</span>
                <span>目標 {{ focusedLeg.knee.target_position }} / {{ degrees(focusedLeg.knee.target_angle_rad) }}</span>
              </div>
            </template>
          </Card>
        </div>
      </SplitterPanel>
    </Splitter>
  </div>
</template>
