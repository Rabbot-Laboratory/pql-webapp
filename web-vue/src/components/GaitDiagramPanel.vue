<script setup lang="ts">
import { computed } from 'vue';

import Card from 'primevue/card';

import { useControlStore, type GaitSample } from '@/stores/control';
import type { LegId } from '@/types/control';
import { legLabel } from '@/utils/i18n';

const store = useControlStore();

const WINDOW_MS = 20_000;
const WIDTH = 800;
const ROW_HEIGHT = 26;
const BAR_HEIGHT = 14;
const LEG_ORDER: LegId[] = ['front_right', 'front_left', 'rear_right', 'rear_left'];
const HEIGHT = ROW_HEIGHT * LEG_ORDER.length + 18;

interface Segment {
  x: number;
  width: number;
}

const windowedSamples = computed<GaitSample[]>(() => {
  const history = store.gaitHistory;
  if (!history.length) return [];
  const end = history[history.length - 1].t;
  return history.filter((sample) => end - sample.t <= WINDOW_MS);
});

function toX(t: number): number {
  const samples = windowedSamples.value;
  const end = samples[samples.length - 1].t;
  return WIDTH - ((end - t) / WINDOW_MS) * WIDTH;
}

/** Merge consecutive same-state samples into contact bars per leg. */
const contactSegments = computed<Record<LegId, Segment[]>>(() => {
  const result = Object.fromEntries(LEG_ORDER.map((leg) => [leg, [] as Segment[]])) as Record<
    LegId,
    Segment[]
  >;
  const samples = windowedSamples.value;
  for (const leg of LEG_ORDER) {
    let start: number | null = null;
    for (let index = 0; index < samples.length; index += 1) {
      const supporting = samples[index].contacts[leg] ?? false;
      if (supporting && start === null) {
        start = samples[index].t;
      }
      const last = index === samples.length - 1;
      if (start !== null && (!supporting || last)) {
        const endT = supporting && last ? samples[index].t : samples[index].t;
        result[leg].push({ x: toX(start), width: Math.max(1, toX(endT) - toX(start)) });
        start = null;
      }
    }
  }
  return result;
});

/** Vertical markers where the walk phase wraps (cycle boundaries). */
const cycleMarkers = computed<number[]>(() => {
  const samples = windowedSamples.value;
  const markers: number[] = [];
  for (let index = 1; index < samples.length; index += 1) {
    const prev = samples[index - 1].phase;
    const current = samples[index].phase;
    if (prev !== null && current !== null && current < prev) {
      markers.push(toX(samples[index].t));
    }
  }
  return markers;
});

/** Shaded spans while the controller was holding at the contact gate. */
const gateSegments = computed<Segment[]>(() => {
  const samples = windowedSamples.value;
  const segments: Segment[] = [];
  let start: number | null = null;
  for (let index = 0; index < samples.length; index += 1) {
    const waiting = samples[index].gateWaiting;
    if (waiting && start === null) start = samples[index].t;
    const last = index === samples.length - 1;
    if (start !== null && (!waiting || last)) {
      segments.push({ x: toX(start), width: Math.max(2, toX(samples[index].t) - toX(start)) });
      start = null;
    }
  }
  return segments;
});

const hasData = computed(() => windowedSamples.value.length > 1);
</script>

<template>
  <Card class="motion-card gait-diagram-card">
    <template #title>ゲイトダイアグラム</template>
    <template #subtitle>
      直近20秒の脚別接地（サーバー判定）。縦線=歩行サイクル境界、橙帯=接地ゲート待ち。
    </template>
    <template #content>
      <div v-if="hasData" class="gait-diagram-scroll">
        <svg
          class="gait-diagram-svg"
          :viewBox="`0 0 ${WIDTH + 80} ${HEIGHT}`"
          preserveAspectRatio="none"
          role="img"
          aria-label="Gait diagram"
        >
          <g transform="translate(80, 0)">
            <rect
              v-for="(segment, index) in gateSegments"
              :key="`gate-${index}`"
              :x="segment.x"
              y="0"
              :width="segment.width"
              :height="ROW_HEIGHT * LEG_ORDER.length"
              class="gait-gate-band"
            />
            <line
              v-for="(x, index) in cycleMarkers"
              :key="`cycle-${index}`"
              :x1="x"
              :x2="x"
              y1="0"
              :y2="ROW_HEIGHT * LEG_ORDER.length"
              class="gait-cycle-line"
            />
            <g v-for="(leg, row) in LEG_ORDER" :key="leg">
              <line
                x1="0"
                :x2="WIDTH"
                :y1="row * ROW_HEIGHT + ROW_HEIGHT / 2"
                :y2="row * ROW_HEIGHT + ROW_HEIGHT / 2"
                class="gait-baseline"
              />
              <rect
                v-for="(segment, index) in contactSegments[leg]"
                :key="index"
                :x="segment.x"
                :y="row * ROW_HEIGHT + (ROW_HEIGHT - BAR_HEIGHT) / 2"
                :width="segment.width"
                :height="BAR_HEIGHT"
                rx="2"
                class="gait-contact-bar"
              />
            </g>
          </g>
          <g>
            <text
              v-for="(leg, row) in LEG_ORDER"
              :key="leg"
              x="4"
              :y="row * ROW_HEIGHT + ROW_HEIGHT / 2 + 4"
              class="gait-leg-label"
            >
              {{ legLabel(leg) }}
            </text>
          </g>
        </svg>
      </div>
      <p v-else class="motion-helper">接地データ待機中（センサ有効時に描画されます）。</p>
    </template>
  </Card>
</template>
