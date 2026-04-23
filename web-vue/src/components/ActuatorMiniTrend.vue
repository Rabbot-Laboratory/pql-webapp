<script setup lang="ts">
import { computed } from 'vue';

import type { TelemetrySample } from '@/types/control';

const props = defineProps<{
  samples: TelemetrySample[];
}>();

const width = 180;
const height = 46;
const padding = { top: 5, right: 4, bottom: 5, left: 4 };
const valueMax = 4095;

function clamp(value: number): number {
  return Math.max(0, Math.min(value, valueMax));
}

function toPolyline(values: number[]): string {
  if (!values.length) {
    return '';
  }
  const innerWidth = width - padding.left - padding.right;
  const innerHeight = height - padding.top - padding.bottom;
  return values
    .map((value, index) => {
      const x = padding.left + (innerWidth * index) / Math.max(values.length - 1, 1);
      const y = padding.top + innerHeight - (innerHeight * clamp(value)) / valueMax;
      return `${x},${y}`;
    })
    .join(' ');
}

const positionPoints = computed(() => toPolyline(props.samples.map((sample) => sample.position)));
const targetPoints = computed(() => toPolyline(props.samples.map((sample) => sample.target_position)));
const lastSample = computed(() => props.samples.at(-1) ?? null);
</script>

<template>
  <div class="mini-trend">
    <svg class="mini-trend-svg" :viewBox="`0 0 ${width} ${height}`" preserveAspectRatio="none" aria-label="actuator mini trend">
      <polyline :points="targetPoints" class="mini-trend-line target" />
      <polyline :points="positionPoints" class="mini-trend-line current" />
    </svg>
    <div class="mini-trend-meta" v-if="lastSample">
      <span>C {{ lastSample.position }}</span>
      <span>T {{ lastSample.target_position }}</span>
    </div>
  </div>
</template>
