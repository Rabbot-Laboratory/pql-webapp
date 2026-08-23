<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue';

import Button from 'primevue/button';
import Tag from 'primevue/tag';

import { useControlStore } from '@/stores/control';
import type { WebGamepadUpdate } from '@/types/control';

const store = useControlStore();
const browserInputEnabled = ref(false);
const browserDeviceName = ref<string | null>(null);
const browserApiAvailable = 'getGamepads' in navigator;
let pollTimer: number | null = null;
let lastBrowserConnected = false;

const sourceLabel = computed(() => {
  if (store.gamepad?.source === 'local') return 'Raspberry Pi USB';
  if (store.gamepad?.source === 'web') return 'Browser Gamepad API';
  return 'No source';
});

const connectionSeverity = computed(() => {
  if (store.gamepad?.connected && !store.gamepad.stale) return 'success';
  if (store.gamepad?.stale) return 'warn';
  return 'secondary';
});

const activeButtons = computed(() =>
  Object.entries(store.gamepad?.buttons ?? {})
    .filter(([, pressed]) => pressed)
    .map(([name]) => name.toUpperCase()),
);

const lastUpdateLabel = computed(() => {
  const timestamp = store.gamepad?.updated_at;
  if (!timestamp) return '-';
  const parsed = new Date(timestamp);
  return Number.isNaN(parsed.getTime()) ? timestamp : parsed.toLocaleTimeString();
});

function clamp(value: number, minimum = -1, maximum = 1): number {
  return Math.min(maximum, Math.max(minimum, value));
}

function axis(name: string): number {
  return clamp(store.gamepad?.axes?.[name] ?? 0);
}

function trigger(name: string): number {
  return clamp(store.gamepad?.axes?.[name] ?? 0, 0, 1);
}

function pressed(name: string): boolean {
  return store.gamepad?.buttons?.[name] ?? false;
}

function stickStyle(xName: string, yName: string): Record<string, string> {
  return {
    left: `${50 + axis(xName) * 31}%`,
    top: `${50 + axis(yName) * 31}%`,
  };
}

function formatAxis(value: number): string {
  const normalized = Math.abs(value) < 0.01 ? 0 : value;
  return normalized.toFixed(2);
}

function browserUpdate(gamepad: Gamepad | null): WebGamepadUpdate {
  if (!gamepad) {
    return {
      connected: false,
      id: browserDeviceName.value ?? 'browser gamepad',
      index: 0,
      mapping: '',
      axes: [],
      buttons: [],
    };
  }
  return {
    connected: true,
    id: gamepad.id,
    index: gamepad.index,
    mapping: gamepad.mapping,
    axes: Array.from(gamepad.axes, (value) => Number(value.toFixed(5))),
    buttons: gamepad.buttons.map((button) => Number(button.value.toFixed(5))),
  };
}

function pollBrowserGamepad(): void {
  const gamepad = Array.from(navigator.getGamepads()).find((item): item is Gamepad => item !== null) ?? null;
  browserDeviceName.value = gamepad?.id ?? null;
  if (gamepad) {
    store.sendWebGamepadUpdate(browserUpdate(gamepad));
    lastBrowserConnected = true;
  } else if (lastBrowserConnected) {
    store.sendWebGamepadUpdate(browserUpdate(null));
    lastBrowserConnected = false;
  }
}

function stopBrowserInput(): void {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer);
    pollTimer = null;
  }
  if (lastBrowserConnected) store.sendWebGamepadUpdate(browserUpdate(null));
  lastBrowserConnected = false;
  browserDeviceName.value = null;
  browserInputEnabled.value = false;
}

function toggleBrowserInput(): void {
  if (browserInputEnabled.value) {
    stopBrowserInput();
    return;
  }
  if (!browserApiAvailable) return;
  browserInputEnabled.value = true;
  pollBrowserGamepad();
  pollTimer = window.setInterval(pollBrowserGamepad, 50);
}

onBeforeUnmount(stopBrowserInput);
</script>

<template>
  <section class="gamepad-page">
    <header class="gamepad-header">
      <div class="gamepad-title">
        <div class="connection-dot" :class="{ connected: store.gamepad?.connected && !store.gamepad.stale }" />
        <div>
          <p>OPERATOR INPUT</p>
          <h2>F710 Controller</h2>
        </div>
      </div>
      <div class="gamepad-tags">
        <Tag :severity="connectionSeverity" :value="store.gamepad?.connected ? 'CONNECTED' : 'DISCONNECTED'" />
        <Tag :severity="store.gamepad?.deadman ? 'success' : 'secondary'" :value="`LB DEADMAN: ${store.gamepad?.deadman ? 'HELD' : 'RELEASED'}`" />
        <Tag severity="warn" value="OBSERVATION ONLY" />
      </div>
    </header>

    <div class="safety-strip">
      <i class="pi pi-eye" />
      入力表示・ログ記録のみ。アクチュエータ出力には接続されていません。
    </div>

    <main class="gamepad-stage">
      <div class="controller-wrap" :class="{ disconnected: !store.gamepad?.connected || store.gamepad.stale }">
        <div class="trigger trigger-left">
          <span>LT</span>
          <div><i :style="{ width: `${trigger('left_trigger') * 100}%` }" /></div>
          <strong>{{ formatAxis(trigger('left_trigger')) }}</strong>
        </div>
        <div class="trigger trigger-right">
          <span>RT</span>
          <div><i :style="{ width: `${trigger('right_trigger') * 100}%` }" /></div>
          <strong>{{ formatAxis(trigger('right_trigger')) }}</strong>
        </div>

        <button class="shoulder shoulder-left" :class="{ pressed: pressed('lb') }" type="button">LB</button>
        <button class="shoulder shoulder-right" :class="{ pressed: pressed('rb') }" type="button">RB</button>

        <div class="controller-body">
          <div class="grip grip-left" />
          <div class="grip grip-right" />

          <div class="stick-block left-stick-block">
            <div class="stick-ring" :class="{ pressed: pressed('btn_thumbl') }">
              <span class="stick-cross horizontal" />
              <span class="stick-cross vertical" />
              <span class="stick-knob" :style="stickStyle('left_x', 'left_y')">L3</span>
            </div>
            <div class="stick-values">
              <span>X {{ formatAxis(axis('left_x')) }}</span>
              <span>Y {{ formatAxis(axis('left_y')) }}</span>
            </div>
          </div>

          <div class="dpad" aria-label="D-pad">
            <span class="dpad-center" />
            <button class="dpad-button dpad-up" :class="{ pressed: axis('dpad_y') < -0.5 }" type="button">▲</button>
            <button class="dpad-button dpad-right" :class="{ pressed: axis('dpad_x') > 0.5 }" type="button">▶</button>
            <button class="dpad-button dpad-down" :class="{ pressed: axis('dpad_y') > 0.5 }" type="button">▼</button>
            <button class="dpad-button dpad-left" :class="{ pressed: axis('dpad_x') < -0.5 }" type="button">◀</button>
          </div>

          <div class="center-controls">
            <div class="center-button-row">
              <button :class="{ pressed: pressed('back') }" type="button">BACK</button>
              <button class="controller-logo" :class="{ pressed: pressed('btn_mode') }" type="button">logi</button>
              <button :class="{ pressed: pressed('start') }" type="button">START</button>
            </div>
            <div class="hardware-button-row">
              <span>MODE</span>
              <i title="Mode status" />
              <span>VIBRATION</span>
            </div>
            <div class="deadman-indicator" :class="{ active: store.gamepad?.deadman }">
              <i class="pi pi-shield" />
              {{ store.gamepad?.deadman ? 'DEADMAN HELD' : 'HOLD LB FOR DEADMAN' }}
            </div>
          </div>

          <div class="face-buttons" aria-label="ABXY buttons">
            <button class="face-button face-y" :class="{ pressed: pressed('y') }" type="button">Y</button>
            <button class="face-button face-b" :class="{ pressed: pressed('b') }" type="button">B</button>
            <button class="face-button face-a" :class="{ pressed: pressed('a') }" type="button">A</button>
            <button class="face-button face-x" :class="{ pressed: pressed('x') }" type="button">X</button>
          </div>

          <div class="stick-block right-stick-block">
            <div class="stick-ring" :class="{ pressed: pressed('btn_thumbr') }">
              <span class="stick-cross horizontal" />
              <span class="stick-cross vertical" />
              <span class="stick-knob" :style="stickStyle('right_x', 'right_y')">R3</span>
            </div>
            <div class="stick-values">
              <span>X {{ formatAxis(axis('right_x')) }}</span>
              <span>Y {{ formatAxis(axis('right_y')) }}</span>
            </div>
          </div>
        </div>

        <div v-if="!store.gamepad?.connected || store.gamepad.stale" class="controller-overlay">
          <i class="pi pi-link" />
          <strong>CONTROLLER DISCONNECTED</strong>
        </div>
      </div>
    </main>

    <footer class="gamepad-footer">
      <div class="source-summary">
        <span><i class="pi pi-desktop" /> {{ sourceLabel }}</span>
        <strong>{{ store.gamepad?.device_name ?? '-' }}</strong>
        <span>{{ store.gamepad?.mapping ?? '-' }}</span>
        <span>更新 {{ lastUpdateLabel }}</span>
      </div>
      <div class="active-summary" :class="{ active: activeButtons.length }">
        <span>押下中</span>
        <strong>{{ activeButtons.length ? activeButtons.join(' · ') : 'なし' }}</strong>
      </div>
      <Button
        size="small"
        :label="browserInputEnabled ? 'PC入力を停止' : 'PC入力を有効化'"
        :severity="browserInputEnabled ? 'danger' : 'secondary'"
        :outlined="!browserInputEnabled"
        :disabled="!browserApiAvailable || store.wsState !== 'live'"
        @click="toggleBrowserInput"
      />
    </footer>
  </section>
</template>

<style scoped>
.gamepad-page {
  display: grid;
  grid-template-rows: auto auto minmax(0, 1fr) auto;
  gap: .55rem;
  width: 100%;
  height: 100%;
  min-height: 0;
  overflow: hidden;
}
.gamepad-header { display: flex; align-items: center; justify-content: space-between; gap: 1rem; }
.gamepad-title { display: flex; align-items: center; gap: .7rem; }
.gamepad-title p { margin: 0; color: var(--p-primary-color); font-size: .68rem; font-weight: 800; letter-spacing: .14em; }
.gamepad-title h2 { margin: .05rem 0 0; font-size: 1.25rem; line-height: 1.1; }
.connection-dot { width: .75rem; height: .75rem; border-radius: 50%; background: var(--p-red-500); box-shadow: 0 0 0 .28rem color-mix(in srgb, var(--p-red-500) 18%, transparent); }
.connection-dot.connected { background: var(--p-green-400); box-shadow: 0 0 0 .28rem color-mix(in srgb, var(--p-green-400) 18%, transparent), 0 0 1rem color-mix(in srgb, var(--p-green-400) 55%, transparent); }
.gamepad-tags { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: .4rem; }
.safety-strip { display: flex; align-items: center; gap: .5rem; padding: .45rem .75rem; border: 1px solid color-mix(in srgb, var(--p-orange-500) 40%, transparent); border-radius: .55rem; background: color-mix(in srgb, var(--p-orange-500) 8%, transparent); color: var(--text-color-secondary); font-size: .78rem; }
.safety-strip i { color: var(--p-orange-400); }

.gamepad-stage { display: grid; place-items: center; min-height: 0; overflow: hidden; }
.controller-wrap { position: relative; width: min(100%, 51rem, calc((100vh - 15rem) * 1.72)); min-width: 32rem; aspect-ratio: 1.72; }
.controller-wrap.disconnected .controller-body { filter: saturate(.25) brightness(.72); }
.controller-body { position: absolute; inset: 13% 4% 3%; border: 1px solid #87919d; border-radius: 38% 38% 28% 28% / 29% 29% 49% 49%; background: radial-gradient(circle at 50% 28%, #f1f3f5 0, #c6ccd2 47%, #8d969f 100%); box-shadow: inset 0 2px 2px rgba(255,255,255,.9), inset 0 -1rem 2.5rem rgba(38,47,57,.28), 0 1.3rem 2.5rem rgba(0,0,0,.32); }
.controller-body::before { content: ''; position: absolute; inset: 6% 8% 12%; border: 1px solid rgba(255,255,255,.42); border-radius: inherit; pointer-events: none; }
.grip { position: absolute; z-index: -1; bottom: -11%; width: 31%; height: 55%; border: 1px solid #3b485a; background: linear-gradient(150deg, #252f3e, #111822); box-shadow: 0 1rem 1.8rem rgba(0,0,0,.3); }
.grip-left { left: 3%; border-radius: 70% 25% 60% 45%; transform: rotate(13deg); }
.grip-right { right: 3%; border-radius: 25% 70% 45% 60%; transform: rotate(-13deg); }

.trigger { position: absolute; top: 0; display: grid; grid-template-columns: 2rem 5.2rem 2.3rem; align-items: center; gap: .35rem; z-index: 4; color: var(--text-color-secondary); font-size: .7rem; font-variant-numeric: tabular-nums; }
.trigger-left { left: 9%; }
.trigger-right { right: 9%; }
.trigger > span { font-weight: 800; color: var(--text-color); }
.trigger > div { height: .42rem; overflow: hidden; border-radius: 999px; background: #101722; box-shadow: inset 0 0 0 1px #465267; }
.trigger > div i { display: block; height: 100%; border-radius: inherit; background: linear-gradient(90deg, var(--p-cyan-600), var(--p-cyan-300)); transition: width 45ms linear; }
.trigger strong { text-align: right; }
.shoulder { position: absolute; top: 8%; z-index: 5; width: 29%; height: 10%; border: 1px solid #68778b; border-radius: 1rem 1rem .45rem .45rem; background: linear-gradient(#39475a, #1b2532); color: #dce5ef; font-weight: 800; letter-spacing: .08em; transition: 45ms ease; }
.shoulder-left { left: 9%; }
.shoulder-right { right: 9%; }
.shoulder.pressed { border-color: var(--p-green-400); background: linear-gradient(var(--p-green-500), var(--p-green-700)); box-shadow: 0 0 1.2rem color-mix(in srgb, var(--p-green-400) 55%, transparent); transform: translateY(2px); }

.stick-block { position: absolute; width: 24%; text-align: center; }
.left-stick-block { left: 25%; top: 56%; }
.right-stick-block { right: 25%; top: 56%; }
.stick-ring { position: relative; width: min(10.2vw, 7.2rem); max-width: 100%; aspect-ratio: 1; margin: auto; border: .45rem solid #111824; border-radius: 50%; background: radial-gradient(circle, #303c4d 0 48%, #161e2a 50% 100%); box-shadow: inset 0 0 0 1px #5a687a, 0 .45rem .8rem rgba(0,0,0,.35); }
.stick-ring.pressed { border-color: var(--p-green-700); box-shadow: 0 0 1.1rem color-mix(in srgb, var(--p-green-400) 45%, transparent); }
.stick-cross { position: absolute; left: 50%; top: 50%; background: rgba(255,255,255,.11); transform: translate(-50%,-50%); }
.stick-cross.horizontal { width: 68%; height: 1px; }
.stick-cross.vertical { width: 1px; height: 68%; }
.stick-knob { position: absolute; display: grid; place-items: center; width: 42%; aspect-ratio: 1; border: 1px solid #8492a5; border-radius: 50%; background: radial-gradient(circle at 35% 30%, #67758a, #263242 70%); box-shadow: 0 .25rem .5rem rgba(0,0,0,.45); color: #e9f2fb; font-size: .64rem; font-weight: 800; transform: translate(-50%,-50%); transition: left 35ms linear, top 35ms linear; }
.stick-values { display: flex; justify-content: center; gap: .55rem; margin-top: .25rem; color: #9fb0c3; font-size: .64rem; font-variant-numeric: tabular-nums; }

.dpad { position: absolute; left: 13%; top: 20%; width: 20%; aspect-ratio: 1; }
.dpad-button, .dpad-center { position: absolute; width: 35%; height: 35%; border: 1px solid #566477; background: linear-gradient(#394657, #202a37); color: #aebccc; font-size: .72rem; }
.dpad-up { left: 32.5%; top: 0; border-radius: .45rem .45rem 0 0; }
.dpad-right { right: 0; top: 32.5%; border-radius: 0 .45rem .45rem 0; }
.dpad-down { left: 32.5%; bottom: 0; border-radius: 0 0 .45rem .45rem; }
.dpad-left { left: 0; top: 32.5%; border-radius: .45rem 0 0 .45rem; }
.dpad-center { left: 32.5%; top: 32.5%; border: 0; }
.dpad-button.pressed { z-index: 2; border-color: var(--p-green-400); background: var(--p-green-600); color: white; box-shadow: 0 0 .8rem color-mix(in srgb, var(--p-green-400) 55%, transparent); }

.center-controls { position: absolute; left: 37%; top: 24%; width: 27%; text-align: center; }
.center-button-row { display: grid; grid-template-columns: 1fr 1.35fr 1fr; align-items: center; gap: .4rem; }
.center-button-row button { padding: .35rem .25rem; border: 1px solid #596675; border-radius: 999px; background: #222d3c; color: #d3dbe5; font-size: .55rem; font-weight: 800; }
.center-button-row button.pressed { border-color: var(--p-green-400); background: var(--p-green-600); color: white; box-shadow: 0 0 .7rem color-mix(in srgb, var(--p-green-400) 50%, transparent); }
.controller-logo { display: grid; place-items: center; aspect-ratio: 1.6; border: 1px solid #596675 !important; border-radius: 50% !important; background: linear-gradient(#354253,#151d28) !important; color: #e2e8ef !important; font-size: .68rem !important; font-style: italic; font-weight: 900 !important; letter-spacing: -.03em; }
.controller-logo.pressed { border-color: var(--p-green-400) !important; background: var(--p-green-600) !important; }
.hardware-button-row { display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; gap: .45rem; margin-top: .48rem; color: #3c4856; font-size: .48rem; font-weight: 900; letter-spacing: .04em; }
.hardware-button-row span { padding: .22rem .18rem; border: 1px solid #73808d; border-radius: 999px; background: #aeb6be; box-shadow: inset 0 1px 1px rgba(255,255,255,.65); }
.hardware-button-row i { width: .42rem; height: .42rem; border: 1px solid #596573; border-radius: 50%; background: #25303d; }
.deadman-indicator { margin-top: .45rem; padding: .28rem .4rem; border: 1px solid #697583; border-radius: .35rem; background: rgba(24,32,42,.83); color: #9aaabd; font-size: .52rem; font-weight: 800; letter-spacing: .04em; }
.deadman-indicator.active { border-color: var(--p-green-400); background: color-mix(in srgb, var(--p-green-500) 18%, transparent); color: var(--p-green-300); box-shadow: 0 0 .8rem color-mix(in srgb, var(--p-green-400) 35%, transparent); }

.face-buttons { position: absolute; right: 12%; top: 20%; width: 22%; aspect-ratio: 1; }
.face-button { position: absolute; display: grid; place-items: center; width: 36%; aspect-ratio: 1; border: .2rem solid rgba(255,255,255,.16); border-radius: 50%; background: #263141; color: white; font-size: .9rem; font-weight: 900; box-shadow: 0 .25rem .45rem rgba(0,0,0,.35); transition: 45ms ease; }
.face-y { left: 32%; top: 0; color: #f5d54f; }
.face-b { right: 0; top: 32%; color: #ef5e65; }
.face-a { left: 32%; bottom: 0; color: #55d686; }
.face-x { left: 0; top: 32%; color: #62aef3; }
.face-button.pressed { transform: translateY(2px) scale(.92); color: white; border-color: white; box-shadow: 0 0 1rem currentColor; }
.face-a.pressed { background: #229653; }
.face-b.pressed { background: #c73e45; }
.face-x.pressed { background: #287fc9; }
.face-y.pressed { background: #b89516; }

.controller-overlay { position: absolute; inset: 16% 8% 8%; z-index: 10; display: grid; place-content: center; justify-items: center; gap: .6rem; border-radius: 3rem; background: rgba(8,12,18,.58); color: #ef7474; letter-spacing: .08em; backdrop-filter: blur(2px); }
.controller-overlay i { font-size: 1.8rem; }

.gamepad-footer { display: grid; grid-template-columns: minmax(0,1.5fr) minmax(12rem,.7fr) auto; align-items: center; gap: .7rem; padding: .48rem .65rem; border: 1px solid var(--surface-border); border-radius: .6rem; background: color-mix(in srgb, var(--surface-card) 84%, transparent); font-size: .72rem; }
.source-summary { display: flex; align-items: center; gap: .65rem; min-width: 0; color: var(--text-color-secondary); }
.source-summary strong { overflow: hidden; color: var(--text-color); text-overflow: ellipsis; white-space: nowrap; }
.active-summary { display: flex; align-items: center; gap: .5rem; min-width: 0; color: var(--text-color-secondary); }
.active-summary strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.active-summary.active { color: var(--p-green-400); }

@media (max-width: 900px) {
  .gamepad-tags :deep(.p-tag:last-child) { display: none; }
  .controller-wrap { min-width: 27rem; width: min(100%, calc((100vh - 14rem) * 1.72)); }
  .gamepad-footer { grid-template-columns: 1fr auto; }
  .active-summary { display: none; }
  .source-summary span:nth-of-type(n+2) { display: none; }
}
@media (max-width: 600px) {
  .gamepad-header { align-items: flex-start; }
  .gamepad-title h2 { font-size: 1rem; }
  .gamepad-tags { max-width: 50%; }
  .gamepad-tags :deep(.p-tag:nth-child(2)) { display: none; }
  .safety-strip { font-size: .68rem; }
  .controller-wrap { min-width: 22rem; }
  .trigger { grid-template-columns: 1.5rem 3.5rem 2rem; }
}
</style>
