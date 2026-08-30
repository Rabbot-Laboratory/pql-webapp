<script setup lang="ts">
import { computed, ref } from 'vue';

import Button from 'primevue/button';
import Card from 'primevue/card';
import Checkbox from 'primevue/checkbox';
import Tag from 'primevue/tag';
import { useToast } from 'primevue/usetoast';

import { useControlStore } from '@/stores/control';

const AXIS_LABELS = ['右前股', '右前膝', '左前股', '左前膝', '右後股', '右後膝', '左後股', '左後膝'];

const store = useControlStore();
const toast = useToast();
const safetyConfirmed = ref(false);
const busy = ref(false);

const standing = computed(() => store.standing);
const phaseLabel = computed(() => {
  if (!standing.value?.enabled) return standing.value?.auto_disabled ? '安全停止' : '停止中';
  if (standing.value.phase === 'rising') return '上昇中';
  if (standing.value.auto_ok) return '立位OK';
  if (standing.value.manual_ok) return '立位OK(手動承認)';
  return '保持中';
});
const phaseSeverity = computed(() => {
  if (!standing.value?.enabled) return standing.value?.auto_disabled ? 'danger' : 'secondary';
  if (standing.value.auto_ok) return 'success';
  if (standing.value.manual_ok) return 'warn';
  return standing.value.phase === 'rising' ? 'info' : 'warn';
});
const axisRows = computed(() => {
  const state = standing.value;
  if (!state) return [];
  return state.axis_errors.map((error, index) => ({
    label: AXIS_LABELS[index] ?? `#${index}`,
    error,
    overdrive: state.overdrive_active[index] ?? false,
  }));
});

async function approve(value: boolean): Promise<void> {
  busy.value = true;
  try {
    await store.setStandingManualOk(value);
  } catch (error) {
    toast.add({
      severity: 'error',
      summary: '承認を変更できません',
      detail: error instanceof Error ? error.message : undefined,
      life: 3500,
    });
  } finally {
    busy.value = false;
  }
}

async function toggle(): Promise<void> {
  const enable = !standing.value?.enabled;
  busy.value = true;
  try {
    await store.setStandingEnabled(enable, enable && safetyConfirmed.value);
  } catch (error) {
    toast.add({
      severity: 'error',
      summary: enable ? '立位を開始できません' : '立位を停止できません',
      detail: error instanceof Error ? error.message : undefined,
      life: 3500,
    });
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <Card class="motion-card standing-card">
    <template #title>安全に立つ(立位保持)</template>
    <template #subtitle>
      Home姿勢へゆっくり上昇し、不感帯を突破する補正で立位を保持します。
      立位OKになると歩行ボタンが有効になります。
    </template>
    <template #content>
      <div class="standing-controls">
        <div class="motion-check-row">
          <Checkbox v-model="safetyConfirmed" binary input-id="standing-safety" :disabled="standing?.enabled" />
          <label for="standing-safety">周囲の安全、支持具、空気遮断手段を確認しました</label>
        </div>
        <Button
          :label="standing?.enabled ? '立位を解除' : '立ち上がる'"
          :icon="standing?.enabled ? 'pi pi-stop' : 'pi pi-arrow-up'"
          :severity="standing?.enabled ? 'secondary' : 'success'"
          :disabled="busy || (!standing?.enabled && !safetyConfirmed)"
          @click="toggle"
        />
        <Button
          v-if="standing?.enabled && !standing.auto_ok"
          :label="standing.manual_ok ? '承認を取り消す' : '立位OKとして承認'"
          :icon="standing.manual_ok ? 'pi pi-times' : 'pi pi-check'"
          :severity="standing.manual_ok ? 'secondary' : 'warn'"
          :disabled="busy"
          @click="approve(!standing.manual_ok)"
        />
        <div class="motion-meta-row">
          <Tag :severity="phaseSeverity" :value="phaseLabel" />
          <Tag
            severity="info"
            :value="`Roll ${(standing?.roll_deg ?? 0).toFixed(1)}° / Pitch ${(standing?.pitch_deg ?? 0).toFixed(1)}°`"
          />
        </div>
      </div>
      <div v-if="axisRows.length" class="standing-axis-grid">
        <span
          v-for="row in axisRows"
          :key="row.label"
          class="standing-axis-chip"
          :class="{ 'is-overdrive': row.overdrive, 'is-large': Math.abs(row.error) > 200 }"
          :title="row.overdrive ? 'オーバードライブ補正中' : ''"
        >
          {{ row.label }}: {{ row.error > 0 ? '+' : '' }}{{ row.error }}
        </span>
      </div>
      <p v-if="standing?.enabled && !standing.auto_ok" class="motion-helper">
        自動判定が出ない場合、目視で立っていると確認できたら「立位OKとして承認」で歩行ゲートを開けます。
        承認は安全確認の代わりにはなりません。立位の解除・安全停止・歩行への移行で自動的に取り消されます。
      </p>
      <p v-if="standing?.disabled_reason && !standing.enabled" class="adaptive-walk-stop-reason">
        停止理由: {{ standing.disabled_reason }}
      </p>
    </template>
  </Card>
</template>
