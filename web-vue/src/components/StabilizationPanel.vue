<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue';

import Button from 'primevue/button';
import Card from 'primevue/card';
import Column from 'primevue/column';
import DataTable from 'primevue/datatable';
import InputNumber from 'primevue/inputnumber';
import Tag from 'primevue/tag';
import ToggleSwitch from 'primevue/toggleswitch';
import { useConfirm } from 'primevue/useconfirm';

import type { StabilizationGains, StabilizationState } from '@/types/control';
import { actuatorLabel } from '@/utils/i18n';

const props = defineProps<{
  stabilization: StabilizationState | null;
  toggleBusy?: boolean;
  gainsBusy?: boolean;
  gainsError?: boolean;
}>();

const emit = defineEmits<{
  toggleEnabled: [enabled: boolean];
  applyGains: [gains: StabilizationGains];
}>();

const confirm = useConfirm();

const DEFAULT_GAINS: StabilizationGains = {
  kp_roll: 0,
  ki_roll: 0,
  kd_roll: 0,
  kp_pitch: 0,
  ki_pitch: 0,
  kd_pitch: 0,
};

// Local editable copy of the gains. `dirty` tracks unsaved edits so live WS
// updates don't clobber the operator's in-progress typing. It stays true for
// the whole apply round-trip (`gainsBusy`, owned by App.vue, is true from the
// moment Apply is clicked until the POST resolves) - otherwise an ~8Hz
// `stabilization_state` push carrying pre-apply gains could overwrite the
// operator's just-submitted values before the response lands. `dirty` is only
// cleared once `gainsBusy` transitions back to false, at which point the form
// is resynced from the now-authoritative server state.
const dirty = ref(false);
const gainsForm = reactive<StabilizationGains>({ ...DEFAULT_GAINS });

watch(
  () => props.stabilization?.gains,
  (gains) => {
    if (!gains || dirty.value || props.gainsBusy) {
      return;
    }
    Object.assign(gainsForm, gains);
  },
  { immediate: true, deep: true },
);

watch(
  () => props.gainsBusy,
  (busy, wasBusy) => {
    // Only resync from server state when the apply round-trip SUCCEEDED.
    // On failure (`gainsError`), keep `dirty` and the operator's edited
    // values — resyncing would silently discard what they just typed.
    if (wasBusy && !busy && !props.gainsError) {
      dirty.value = false;
      if (props.stabilization?.gains) {
        Object.assign(gainsForm, props.stabilization.gains);
      }
    }
  },
);

function markDirty(): void {
  dirty.value = true;
}

const enabledValue = computed(() => props.stabilization?.enabled ?? false);
const toggleDisabled = computed(() => props.toggleBusy || !props.stabilization);
const gainsDisabled = computed(() => props.gainsBusy || !props.stabilization);

const statusSeverity = computed(() => {
  if (!props.stabilization?.enabled) {
    return 'secondary';
  }
  return props.stabilization.active ? 'success' : 'warning';
});

const statusLabel = computed(() => {
  if (!props.stabilization) {
    return '不明';
  }
  if (!props.stabilization.enabled) {
    return '無効';
  }
  return props.stabilization.active ? '動作中' : '待機中';
});

function formatSigned(value: number | undefined, digits = 1): string {
  if (value === undefined || Number.isNaN(value)) {
    return '-';
  }
  const rounded = value.toFixed(digits);
  return value > 0 ? `+${rounded}` : rounded;
}

function formatRate(value: number | undefined): string {
  return value === undefined ? '-' : value.toFixed(1);
}

function handleToggleRequest(next: boolean): void {
  if (props.toggleBusy) {
    return;
  }
  if (!next) {
    emit('toggleEnabled', false);
    return;
  }
  confirm.require({
    header: 'スタビライゼーションを有効化',
    message: '実機のアクチュエータが動作します。有効化しますか?',
    icon: 'pi pi-exclamation-triangle',
    acceptLabel: '有効化する',
    rejectLabel: 'キャンセル',
    acceptProps: { severity: 'danger' },
    rejectProps: { severity: 'secondary', outlined: true },
    accept: () => emit('toggleEnabled', true),
  });
}

function handleApplyGains(): void {
  // Keep `dirty` true through the apply round-trip; see the watcher above for when it's
  // safe to clear it (once `gainsBusy` goes back to false).
  emit('applyGains', { ...gainsForm });
}
</script>

<template>
  <Card class="stabilization-card">
    <template #title>Stabilization</template>
    <template #subtitle>IMUのロール/ピッチ誤差から脚アクチュエータへの姿勢補正を行います。</template>

    <template #content>
      <div class="stabilization-layout">
        <section class="stabilization-status">
          <div class="stabilization-header">
            <div>
              <h3>制御ステータス</h3>
              <p>ON/OFFと安全機構の状態</p>
            </div>
            <div class="stabilization-toggle">
              <ToggleSwitch
                :model-value="enabledValue"
                :disabled="toggleDisabled"
                @update:model-value="handleToggleRequest"
              />
              <span>{{ enabledValue ? '有効' : '無効' }}</span>
            </div>
          </div>

          <div class="stabilization-tags">
            <Tag :severity="statusSeverity" :value="statusLabel" />
            <Tag
              v-if="stabilization?.auto_disabled"
              severity="danger"
              :value="`自動無効化: ${stabilization.disabled_reason ?? '不明な理由'}`"
            />
            <Tag v-if="stabilization?.attitude_stale" severity="warning" value="IMUデータが古い" />
          </div>

          <div class="stabilization-metrics">
            <article>
              <span>Roll誤差</span>
              <strong>{{ formatSigned(stabilization?.roll_error_deg) }} deg</strong>
            </article>
            <article>
              <span>Pitch誤差</span>
              <strong>{{ formatSigned(stabilization?.pitch_error_deg) }} deg</strong>
            </article>
            <article>
              <span>Roll実測</span>
              <strong>{{ formatSigned(stabilization?.roll_deg) }} deg</strong>
            </article>
            <article>
              <span>Pitch実測</span>
              <strong>{{ formatSigned(stabilization?.pitch_deg) }} deg</strong>
            </article>
            <article>
              <span>ループレート</span>
              <strong>{{ formatRate(stabilization?.loop_rate_hz) }} Hz</strong>
            </article>
          </div>
        </section>

        <section class="stabilization-gains">
          <h3>ゲイン (P / I / D)</h3>
          <p>編集後は「適用」で送信されます(入力中は自動送信されません)。</p>

          <div class="gains-grid">
            <label>
              <span>Roll Kp</span>
              <InputNumber
                v-model="gainsForm.kp_roll"
                :min="0"
                :max="100"
                :min-fraction-digits="2"
                :max-fraction-digits="3"
                :disabled="gainsDisabled"
                @input="markDirty"
              />
            </label>
            <label>
              <span>Roll Ki</span>
              <InputNumber
                v-model="gainsForm.ki_roll"
                :min="0"
                :max="50"
                :min-fraction-digits="2"
                :max-fraction-digits="3"
                :disabled="gainsDisabled"
                @input="markDirty"
              />
            </label>
            <label>
              <span>Roll Kd</span>
              <InputNumber
                v-model="gainsForm.kd_roll"
                :min="0"
                :max="100"
                :min-fraction-digits="2"
                :max-fraction-digits="3"
                :disabled="gainsDisabled"
                @input="markDirty"
              />
            </label>
            <label>
              <span>Pitch Kp</span>
              <InputNumber
                v-model="gainsForm.kp_pitch"
                :min="0"
                :max="100"
                :min-fraction-digits="2"
                :max-fraction-digits="3"
                :disabled="gainsDisabled"
                @input="markDirty"
              />
            </label>
            <label>
              <span>Pitch Ki</span>
              <InputNumber
                v-model="gainsForm.ki_pitch"
                :min="0"
                :max="50"
                :min-fraction-digits="2"
                :max-fraction-digits="3"
                :disabled="gainsDisabled"
                @input="markDirty"
              />
            </label>
            <label>
              <span>Pitch Kd</span>
              <InputNumber
                v-model="gainsForm.kd_pitch"
                :min="0"
                :max="100"
                :min-fraction-digits="2"
                :max-fraction-digits="3"
                :disabled="gainsDisabled"
                @input="markDirty"
              />
            </label>
          </div>

          <Button
            label="ゲインを適用"
            icon="pi pi-check"
            :loading="gainsBusy"
            :disabled="gainsDisabled"
            @click="handleApplyGains"
          />
        </section>

        <section class="stabilization-corrections">
          <h3>アクチュエータ補正値</h3>
          <DataTable :value="stabilization?.corrections ?? []" data-key="actuator_id" class="corrections-table">
            <Column field="actuator_id" header="ID" />
            <Column header="軸">
              <template #body="{ data }">
                {{ actuatorLabel(data.label) }}
              </template>
            </Column>
            <Column header="補正値">
              <template #body="{ data }">
                <span :class="{ 'is-nonzero': Math.abs(data.correction) > 0.001 }">
                  {{ formatSigned(data.correction, 1) }}
                </span>
              </template>
            </Column>
          </DataTable>
        </section>
      </div>
    </template>
  </Card>
</template>
