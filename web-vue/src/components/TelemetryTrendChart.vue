<script setup lang="ts">
import { computed, reactive } from 'vue';

import type { TelemetrySample } from '@/types/control';

const props = defineProps<{
  samples: TelemetrySample[];
  compact?: boolean;
}>();

const width = 760;
const height = computed(() => (props.compact ? 140 : 240));
const padding = { top: 20, right: 18, bottom: 24, left: 36 };
const valueMax = 4095;

type SeriesKey =
  | 'position'
  | 'voltage'
  | 'command'
  | 'pressure'
  | 'targetPosition'
  | 'targetCommand';

const legendItems: Array<{ key: SeriesKey; label: string; color: string; dashed?: boolean }> = [
  { key: 'position', label: 'Position', color: '#d44d2a' },
  { key: 'voltage', label: 'Voltage', color: '#1f7a8c' },
  { key: 'command', label: 'Command', color: '#2b9348' },
  { key: 'pressure', label: 'Pressure', color: '#8a5cf6' },
  { key: 'targetPosition', label: 'Target Position', color: '#ff9966', dashed: true },
  { key: 'targetCommand', label: 'Target Command', color: '#90be6d', dashed: true },
];

const visibleSeries = reactive<Record<SeriesKey, boolean>>({
  position: true,
  voltage: true,
  command: true,
  pressure: true,
  targetPosition: true,
  targetCommand: true,
});

function clamp(value: number): number {
  return Math.max(0, Math.min(value, valueMax));
}

function toPolyline(values: number[]): string {
  if (!values.length) {
    return '';
  }

  const innerWidth = width - padding.left - padding.right;
  const innerHeight = height.value - padding.top - padding.bottom;

  return values
    .map((value, index) => {
      const x = padding.left + (innerWidth * index) / Math.max(values.length - 1, 1);
      const y = padding.top + innerHeight - (innerHeight * clamp(value)) / valueMax;
      return `${x},${y}`;
    })
    .join(' ');
}

const points = computed(() => ({
  position: toPolyline(props.samples.map((sample) => sample.position)),
  voltage: toPolyline(props.samples.map((sample) => sample.voltage)),
  command: toPolyline(props.samples.map((sample) => sample.command)),
  pressure: toPolyline(props.samples.map((sample) => sample.pressure)),
  targetPosition: toPolyline(props.samples.map((sample) => sample.target_position)),
  targetCommand: toPolyline(props.samples.map((sample) => sample.target_command)),
}));

function toggleSeries(key: SeriesKey): void {
  visibleSeries[key] = !visibleSeries[key];
}
</script>

<template>
  <div class="trend-chart" :class="{ 'is-compact': compact }">
    <div class="trend-chart-legend">
      <button
        v-for="item in legendItems"
        :key="item.key"
        type="button"
        class="trend-legend-item"
        :class="{ 'is-hidden': !visibleSeries[item.key] }"
        @click="toggleSeries(item.key)"
      >
        <i class="trend-legend-line" :style="{ '--line-color': item.color, '--line-dash': item.dashed ? '8 6' : 'none' }"></i>
        {{ item.label }}
      </button>
    </div>

    <div v-if="samples.length < 2" class="trend-chart-empty">サンプルがたまるとここにライブグラフを表示します。</div>

    <svg v-else class="trend-chart-svg" :viewBox="`0 0 ${width} ${height}`" preserveAspectRatio="none" aria-label="telemetry trend">
      <line :x1="padding.left" :x2="width - padding.right" :y1="height - padding.bottom" :y2="height - padding.bottom" class="trend-axis" />
      <line :x1="padding.left" :x2="padding.left" :y1="padding.top" :y2="height - padding.bottom" class="trend-axis" />

      <line
        :x1="padding.left"
        :x2="width - padding.right"
        :y1="padding.top + (height - padding.top - padding.bottom) / 2"
        :y2="padding.top + (height - padding.top - padding.bottom) / 2"
        class="trend-axis-subline"
      />

      <g class="trend-axis-labels">
        <text :x="padding.left - 6" :y="padding.top + 4" text-anchor="end">4095</text>
        <text :x="padding.left - 6" :y="padding.top + (height - padding.top - padding.bottom) / 2 + 4" text-anchor="end">2048</text>
        <text :x="padding.left - 6" :y="height - padding.bottom + 4" text-anchor="end">0</text>
        <text :x="width - padding.right" :y="height - padding.bottom + 16" text-anchor="end">Now</text>
        <text :x="padding.left" :y="height - padding.bottom + 16" text-anchor="start">Oldest</text>
      </g>

      <polyline v-if="visibleSeries.targetPosition" :points="points.targetPosition" class="trend-line target-position" />
      <polyline v-if="visibleSeries.targetCommand" :points="points.targetCommand" class="trend-line target-command" />
      <polyline v-if="visibleSeries.position" :points="points.position" class="trend-line position" />
      <polyline v-if="visibleSeries.voltage" :points="points.voltage" class="trend-line voltage" />
      <polyline v-if="visibleSeries.command" :points="points.command" class="trend-line command" />
      <polyline v-if="visibleSeries.pressure" :points="points.pressure" class="trend-line pressure" />
    </svg>
  </div>
</template>
