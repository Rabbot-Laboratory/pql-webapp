<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';

import Button from 'primevue/button';
import Card from 'primevue/card';
import Checkbox from 'primevue/checkbox';
import InputNumber from 'primevue/inputnumber';
import InputText from 'primevue/inputtext';
import SelectButton from 'primevue/selectbutton';
import Tag from 'primevue/tag';
import Textarea from 'primevue/textarea';
import { useToast } from 'primevue/usetoast';

import { useControlStore } from '@/stores/control';
import type { MotionCategory, MotionLibraryItem, PlaybackAdvanceMode } from '@/types/control';

const store = useControlStore();
const toast = useToast();

const legacyCsvInput = ref<HTMLInputElement | null>(null);
const importedLegacyFileName = ref('');
const airOffConfirmed = ref(false);
const csvLoop = ref(false);
const csvIntervalMs = ref(33);
const csvText = ref('');
const saveName = ref('');
const saveCategory = ref<MotionCategory>('custom');
const selectedMotionKey = ref('');
const advanceMode = ref<PlaybackAdvanceMode>('time');
const positionTolerance = ref(160);
const pressureThreshold = ref(0);
const stepTimeoutMs = ref(1500);
const settleTimeMs = ref(100);
const teachingIntervalMs = ref(80);
const teachingRecording = ref(false);
const teachingRows = ref<string[][]>([]);
const teachingStartedAt = ref<number | null>(null);

let teachingTimer: number | null = null;

const saveCategoryOptions = [
  { label: 'Custom Motion', value: 'custom' },
  { label: 'Fixed Motion', value: 'fixed' },
];

const actuatorOrder = computed(() => [...store.actuators].sort((left, right) => left.actuator_id - right.actuator_id));
const actuatorLabels = computed(() => actuatorOrder.value.map((actuator) => actuator.label));
const csvRows = computed(() => parseRows(csvText.value));
const csvPreviewRows = computed(() => csvRows.value.slice(0, 6));
const teachingPreviewRows = computed(() => teachingRows.value.slice(-6));

const playbackStateLabel = computed(() => {
  if (store.system?.playback_status === 'running') return '再生中';
  if (store.system?.playback_status === 'stopping') return '停止処理中';
  return '停止中';
});

const recordingDurationSec = computed(() => {
  void teachingRows.value.length;
  if (!teachingStartedAt.value) return 0;
  return Math.max(0, (Date.now() - teachingStartedAt.value) / 1000);
});

const canStartCsv = computed(
  () =>
    csvRows.value.length > 0 &&
    csvRows.value.every((row) => row.length === actuatorOrder.value.length),
);
const canSaveMotion = computed(() => saveName.value.trim().length > 0 && canStartCsv.value);
const canStartTeaching = computed(
  () => actuatorOrder.value.length > 0 && airOffConfirmed.value && !teachingRecording.value,
);
const canUseFreeMode = computed(() => actuatorOrder.value.length > 0 && airOffConfirmed.value);

function motionKey(category: MotionCategory, name: string): string {
  return `${category}:${name}`;
}

function parseRows(source: string): string[][] {
  return source
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => line.split(',').map((cell) => cell.trim()));
}

function formatRows(rows: string[][]): string {
  return rows.map((row) => row.join(',')).join('\n');
}

function buildEditorCsvContent(): string {
  const lines = [
    `# interval_sec=${csvIntervalMs.value / 1000}`,
    `# loop=${csvLoop.value ? 'true' : 'false'}`,
    `# advance_mode=${advanceMode.value}`,
    `# position_tolerance=${positionTolerance.value}`,
    `# pressure_threshold=${pressureThreshold.value}`,
    `# step_timeout_sec=${stepTimeoutMs.value / 1000}`,
    `# settle_time_sec=${settleTimeMs.value / 1000}`,
    ...csvRows.value.map((row) => row.join(',')),
  ];
  return lines.join('\n');
}

function downloadEditorCsv(): void {
  if (!canStartCsv.value) {
    return;
  }

  const blob = new Blob([buildEditorCsvContent()], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  const fallbackName = importedLegacyFileName.value.replace(/\.csv$/i, '') || 'motion-editor';
  const fileName = `${(saveName.value.trim() || fallbackName).replace(/[<>:"/\\|?*]+/g, '_')}.csv`;

  anchor.href = url;
  anchor.download = fileName;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(url);

  toast.add({
    severity: 'success',
    summary: 'CSV をダウンロードしました',
    detail: fileName,
    life: 1800,
  });
}

function sampleCurrentPositions(): string[] {
  return actuatorOrder.value.map((actuator) => String(actuator.telemetry.position));
}

function openLegacyCsvPicker(): void {
  legacyCsvInput.value?.click();
}

async function handleLegacyCsvSelected(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) {
    return;
  }

  try {
    const content = await file.text();
    const suggestedName = file.name.replace(/\.csv$/i, '');
    const draft = await store.importLegacyCsvDraft(suggestedName, content);
    csvText.value = formatRows(draft.rows);
    csvIntervalMs.value = Math.round(draft.interval_sec * 1000);
    csvLoop.value = draft.loop;
    advanceMode.value = draft.advance_mode;
    positionTolerance.value = draft.position_tolerance;
    pressureThreshold.value = draft.pressure_threshold;
    stepTimeoutMs.value = Math.round(draft.step_timeout_sec * 1000);
    settleTimeMs.value = Math.round(draft.settle_time_sec * 1000);
    saveName.value = draft.suggested_name;
    saveCategory.value = 'custom';
    selectedMotionKey.value = '';
    importedLegacyFileName.value = file.name;
    toast.add({
      severity: 'success',
      summary: draft.source_format === 'modern' ? '新形式 CSV を読み込みました' : '旧GUI形式の CSV を読み込みました',
      detail: `${draft.frame_count} フレーム / ${draft.axis_count} 軸を Motion Editor に展開しました`,
      life: 2200,
    });
  } catch (error) {
    toast.add({
      severity: 'error',
      summary: 'CSV の読み込みに失敗しました',
      detail: error instanceof Error ? error.message : '予期しないエラーです',
      life: 3200,
    });
  } finally {
    input.value = '';
  }
}

async function openMotion(item: MotionLibraryItem): Promise<void> {
  try {
    const detail = await store.loadMotionFile(item.category, item.name);
    selectedMotionKey.value = motionKey(item.category, item.name);
    csvText.value = formatRows(detail.rows);
    csvIntervalMs.value = Math.round((detail.item.interval_sec ?? 1 / 30) * 1000);
    csvLoop.value = detail.item.loop;
    advanceMode.value = detail.item.advance_mode;
    positionTolerance.value = detail.item.position_tolerance;
    pressureThreshold.value = detail.item.pressure_threshold;
    stepTimeoutMs.value = Math.round(detail.item.step_timeout_sec * 1000);
    settleTimeMs.value = Math.round(detail.item.settle_time_sec * 1000);
    saveName.value = detail.item.name;
    saveCategory.value = detail.item.category === 'fixed' ? 'custom' : detail.item.category;
    importedLegacyFileName.value = detail.item.file_name;
    toast.add({
      severity: 'success',
      summary: 'モーションを読み込みました',
      detail: `${detail.item.name} を Motion Editor に読み込みました`,
      life: 1800,
    });
  } catch (error) {
    toast.add({
      severity: 'error',
      summary: 'モーション読み込みに失敗しました',
      detail: error instanceof Error ? error.message : '予期しないエラーです',
      life: 3200,
    });
  }
}

async function playRows(
  rows: string[][],
  intervalSec: number,
  loop: boolean,
  motionName?: string,
  motionCategory?: MotionCategory,
): Promise<void> {
  try {
    await store.startPlayback(rows, intervalSec, loop, {
      motionName,
      motionCategory,
      advanceMode: advanceMode.value,
      positionTolerance: positionTolerance.value,
      pressureThreshold: pressureThreshold.value,
      stepTimeoutSec: stepTimeoutMs.value / 1000,
      settleTimeSec: settleTimeMs.value / 1000,
    });
    toast.add({
      severity: 'success',
      summary: 'CSV 再生を開始しました',
      detail: `${rows.length} フレーム / ${(intervalSec * 1000).toFixed(0)} ms`,
      life: 1800,
    });
  } catch (error) {
    toast.add({
      severity: 'error',
      summary: 'CSV 再生に失敗しました',
      detail: error instanceof Error ? error.message : '予期しないエラーです',
      life: 3200,
    });
  }
}

async function playMotion(item: MotionLibraryItem): Promise<void> {
  try {
    const detail = await store.loadMotionFile(item.category, item.name);
    selectedMotionKey.value = motionKey(item.category, item.name);
    await playRows(
      detail.rows,
      detail.item.interval_sec ?? 1 / 30,
      detail.item.loop,
      detail.item.name,
      detail.item.category,
    );
  } catch (error) {
    toast.add({
      severity: 'error',
      summary: 'モーション再生に失敗しました',
      detail: error instanceof Error ? error.message : '予期しないエラーです',
      life: 3200,
    });
  }
}

async function deleteMotion(item: MotionLibraryItem): Promise<void> {
  if (item.category !== 'custom') {
    return;
  }
  if (!window.confirm(`Custom Motion "${item.name}" を削除しますか？`)) {
    return;
  }

  try {
    await store.deleteMotion(item.category, item.name);
    if (selectedMotionKey.value === motionKey(item.category, item.name)) {
      selectedMotionKey.value = '';
    }
    toast.add({
      severity: 'success',
      summary: 'Custom Motion を削除しました',
      detail: item.name,
      life: 1800,
    });
  } catch (error) {
    toast.add({
      severity: 'error',
      summary: 'Custom Motion の削除に失敗しました',
      detail: error instanceof Error ? error.message : '予期しないエラーです',
      life: 3200,
    });
  }
}

async function saveCurrentMotion(): Promise<void> {
  if (!canSaveMotion.value) {
    return;
  }

  try {
    const detail = await store.saveMotion(
      saveCategory.value,
      saveName.value.trim(),
      csvRows.value,
      csvIntervalMs.value / 1000,
      csvLoop.value,
      advanceMode.value,
      positionTolerance.value,
      pressureThreshold.value,
      stepTimeoutMs.value / 1000,
      settleTimeMs.value / 1000,
    );
    selectedMotionKey.value = motionKey(detail.item.category, detail.item.name);
    toast.add({
      severity: 'success',
      summary: 'モーションを保存しました',
      detail: detail.item.name,
      life: 1800,
    });
  } catch (error) {
    toast.add({
      severity: 'error',
      summary: 'モーション保存に失敗しました',
      detail: error instanceof Error ? error.message : '予期しないエラーです',
      life: 3200,
    });
  }
}

async function stopPlayback(): Promise<void> {
  try {
    await store.stopPlayback();
    toast.add({
      severity: 'success',
      summary: '再生を停止しました',
      detail: '再生中のモーションを停止しました',
      life: 1600,
    });
  } catch (error) {
    toast.add({
      severity: 'error',
      summary: '停止に失敗しました',
      detail: error instanceof Error ? error.message : '予期しないエラーです',
      life: 3200,
    });
  }
}

async function runFreeMode(): Promise<void> {
  try {
    await store.activateFreeMode();
    toast.add({
      severity: 'warn',
      summary: 'Free Mode を有効化しました',
      detail: '全軸へ Command 0 を送信しました',
      life: 1800,
    });
  } catch (error) {
    toast.add({
      severity: 'error',
      summary: 'Free Mode に失敗しました',
      detail: error instanceof Error ? error.message : '予期しないエラーです',
      life: 3200,
    });
  }
}

function startTeaching(): void {
  if (!canStartTeaching.value) {
    return;
  }

  void runFreeMode();
  teachingRows.value = [sampleCurrentPositions()];
  teachingStartedAt.value = Date.now();
  teachingRecording.value = true;

  if (teachingTimer !== null) {
    window.clearInterval(teachingTimer);
  }
  teachingTimer = window.setInterval(() => {
    teachingRows.value = [...teachingRows.value, sampleCurrentPositions()];
  }, Math.max(30, teachingIntervalMs.value));
}

function stopTeaching(): void {
  if (teachingTimer !== null) {
    window.clearInterval(teachingTimer);
    teachingTimer = null;
  }
  teachingRecording.value = false;
}

function clearTeaching(): void {
  stopTeaching();
  teachingRows.value = [];
  teachingStartedAt.value = null;
}

function loadTeachingIntoEditor(): void {
  if (!teachingRows.value.length) {
    return;
  }
  csvText.value = formatRows(teachingRows.value);
  csvIntervalMs.value = teachingIntervalMs.value;
  csvLoop.value = false;
  saveCategory.value = 'custom';
  if (!saveName.value.trim()) {
    saveName.value = `teaching-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}`;
  }
}

async function playEditorRows(): Promise<void> {
  if (!canStartCsv.value) {
    return;
  }
  await playRows(csvRows.value, csvIntervalMs.value / 1000, csvLoop.value, 'Editor Draft', saveCategory.value);
}

async function playTeachingRows(): Promise<void> {
  if (!teachingRows.value.length) {
    return;
  }
  await playRows(teachingRows.value, teachingIntervalMs.value / 1000, false, 'Direct Teaching', 'custom');
}

watch(
  () => teachingIntervalMs.value,
  (nextInterval) => {
    if (!teachingRecording.value) {
      return;
    }
    if (teachingTimer !== null) {
      window.clearInterval(teachingTimer);
    }
    teachingTimer = window.setInterval(() => {
      teachingRows.value = [...teachingRows.value, sampleCurrentPositions()];
    }, Math.max(30, nextInterval));
  },
);

onBeforeUnmount(() => {
  stopTeaching();
});
</script>

<template>
  <section class="motion-page">
    <div class="motion-grid">
      <Card class="motion-card">
        <template #title>Motion Library</template>
        <template #subtitle>`motion/fixed` と `motion/custom` の CSV を管理します。</template>
        <template #content>
          <div class="motion-library-group">
            <div class="motion-library-header">
              <strong>Fixed Motion</strong>
              <Tag severity="contrast" :value="`${store.motionLibrary.fixed.length} files`" />
            </div>
            <div class="motion-library-list">
              <div
                v-for="item in store.motionLibrary.fixed"
                :key="motionKey(item.category, item.name)"
                class="motion-library-item"
                :class="{ 'is-selected': selectedMotionKey === motionKey(item.category, item.name) }"
                @click="openMotion(item)"
              >
                <div class="motion-library-item-main">
                  <strong>{{ item.name }}</strong>
                  <span>
                    {{ item.frame_count }} frames / {{ item.axis_count }} axes /
                    {{ ((item.interval_sec ?? 1 / 30) * 1000).toFixed(0) }} ms
                  </span>
                </div>
                <div class="motion-library-item-actions">
                  <Button label="読込" size="small" text @click.stop="openMotion(item)" />
                  <Button label="再生" size="small" severity="secondary" @click.stop="playMotion(item)" />
                </div>
              </div>
              <div v-if="!store.motionLibrary.fixed.length" class="motion-empty">
                Fixed Motion フォルダに CSV がありません。
              </div>
            </div>
          </div>

          <div class="motion-library-group">
            <div class="motion-library-header">
              <strong>Custom Motion</strong>
              <Tag severity="contrast" :value="`${store.motionLibrary.custom.length} files`" />
            </div>
            <div class="motion-library-list">
              <div
                v-for="item in store.motionLibrary.custom"
                :key="motionKey(item.category, item.name)"
                class="motion-library-item"
                :class="{ 'is-selected': selectedMotionKey === motionKey(item.category, item.name) }"
                @click="openMotion(item)"
              >
                <div class="motion-library-item-main">
                  <strong>{{ item.name }}</strong>
                  <span>
                    {{ item.frame_count }} frames / {{ item.axis_count }} axes /
                    {{ ((item.interval_sec ?? 1 / 30) * 1000).toFixed(0) }} ms
                  </span>
                </div>
                <div class="motion-library-item-actions">
                  <Button label="読込" size="small" text @click.stop="openMotion(item)" />
                  <Button label="再生" size="small" severity="secondary" @click.stop="playMotion(item)" />
                  <Button label="削除" size="small" severity="danger" outlined @click.stop="deleteMotion(item)" />
                </div>
              </div>
              <div v-if="!store.motionLibrary.custom.length" class="motion-empty">
                Custom Motion フォルダはまだ空です。
              </div>
            </div>
          </div>
        </template>
      </Card>

      <Card class="motion-card">
        <template #title>Playback</template>
        <template #subtitle>読み込んだ CSV をそのまま再生できます。</template>
        <template #content>
          <div class="motion-stop-row">
            <Tag severity="contrast" :value="playbackStateLabel" />
            <Button label="停止" icon="pi pi-stop" severity="danger" @click="stopPlayback" />
          </div>

          <div class="motion-check-row">
            <Checkbox v-model="airOffConfirmed" binary input-id="air-off-confirmed" />
            <label for="air-off-confirmed">エアーが切れていることを確認済み</label>
          </div>

          <p class="motion-helper">
            Free Mode は手で姿勢を動かすための補助機能です。Teaching の前段階としても使えます。
          </p>

          <div class="motion-action-row">
            <Button
              label="全軸をフリー化"
              icon="pi pi-unlock"
              severity="warn"
              :disabled="!canUseFreeMode || store.loading"
              @click="runFreeMode"
            />
            <Button
              label="ライブラリ再読込"
              icon="pi pi-refresh"
              severity="secondary"
              @click="store.refreshMotionLibrary()"
            />
          </div>
        </template>
      </Card>
    </div>

    <div class="motion-grid motion-grid-wide">
      <Card class="motion-card">
        <template #title>Motion Editor</template>
        <template #content>
          <input
            ref="legacyCsvInput"
            type="file"
            accept=".csv,text/csv"
            style="display: none"
            @change="handleLegacyCsvSelected"
          />

          <div class="motion-action-row">
            <Button
              label="旧形式CSVをアップロード"
              icon="pi pi-upload"
              severity="secondary"
              @click="openLegacyCsvPicker"
            />
            <Tag v-if="importedLegacyFileName" severity="info" :value="importedLegacyFileName" />
          </div>

          <div class="motion-inline-fields">
            <label class="field compact-field">
              <span>フレーム間隔 [ms]</span>
              <InputNumber v-model="csvIntervalMs" :min="20" :max="1000" />
            </label>
            <div class="motion-check-row motion-check-inline">
              <Checkbox v-model="csvLoop" binary input-id="csv-loop" />
              <label for="csv-loop">ループ再生</label>
            </div>
          </div>

          <Textarea
            v-model="csvText"
            class="motion-textarea"
            auto-resize
            rows="10"
            placeholder="2048,2048,2048,2048,2048,2048,2048,2048"
          />

          <div class="motion-meta-row">
            <Tag severity="contrast" :value="`${csvRows.length} フレーム`" />
            <Tag
              :severity="canStartCsv ? 'success' : 'danger'"
              :value="canStartCsv ? `${actuatorOrder.length} 軸で再生可能` : '行数を確認してください'"
            />
          </div>

          <div v-if="csvPreviewRows.length" class="motion-preview">
            <div class="motion-preview-header">
              <strong>プレビュー</strong>
              <span>{{ actuatorLabels.join(' / ') }}</span>
            </div>
            <pre>{{ csvPreviewRows.map((row) => row.join(', ')).join('\n') }}</pre>
          </div>

          <div class="motion-action-row">
            <Button
              label="CSVダウンロード"
              icon="pi pi-download"
              severity="secondary"
              :disabled="!canStartCsv"
              @click="downloadEditorCsv"
            />
          </div>

          <div class="motion-save-row">
            <label class="field compact-field motion-name-field">
              <span>保存名</span>
              <InputText v-model="saveName" placeholder="new-motion" />
            </label>
            <div class="field">
              <span>保存先</span>
              <SelectButton
                v-model="saveCategory"
                :options="saveCategoryOptions"
                option-label="label"
                option-value="value"
              />
            </div>
          </div>

          <div class="motion-action-row">
            <Button
              label="エディタ再生"
              icon="pi pi-play"
              :disabled="!canStartCsv || store.loading"
              @click="playEditorRows"
            />
            <Button
              label="保存"
              icon="pi pi-save"
              severity="secondary"
              :disabled="!canSaveMotion || store.loading"
              @click="saveCurrentMotion"
            />
          </div>
        </template>
      </Card>

      <Card class="motion-card">
        <template #title>Direct Teaching</template>
        <template #subtitle>Free 状態で姿勢を記録し、そのまま Motion Editor に移せます。</template>
        <template #content>
          <div class="motion-inline-fields">
            <label class="field compact-field">
              <span>記録間隔 [ms]</span>
              <InputNumber v-model="teachingIntervalMs" :min="30" :max="1000" />
            </label>
            <Tag :severity="teachingRecording ? 'danger' : 'contrast'" :value="teachingRecording ? '記録中' : '待機中'" />
          </div>

          <p class="motion-helper">
            Start で Free Mode を有効化し、現在の 8 軸 Position を一定周期で記録します。Stop 後に Editor へ移して保存や再生に使えます。
          </p>

          <div class="motion-action-row">
            <Button
              label="Start"
              icon="pi pi-circle-fill"
              severity="danger"
              :disabled="!canStartTeaching || store.loading"
              @click="startTeaching"
            />
            <Button label="Stop" icon="pi pi-stop" severity="secondary" :disabled="!teachingRecording" @click="stopTeaching" />
            <Button
              label="Editor に移す"
              icon="pi pi-file-edit"
              severity="secondary"
              :disabled="!teachingRows.length"
              @click="loadTeachingIntoEditor"
            />
          </div>

          <div class="motion-meta-row">
            <Tag severity="contrast" :value="`${teachingRows.length} フレーム`" />
            <Tag severity="info" :value="`${recordingDurationSec.toFixed(1)} sec`" />
          </div>

          <div v-if="teachingPreviewRows.length" class="motion-preview">
            <div class="motion-preview-header">
              <strong>最新の記録フレーム</strong>
              <span>{{ actuatorLabels.join(' / ') }}</span>
            </div>
            <pre>{{ teachingPreviewRows.map((row) => row.join(', ')).join('\n') }}</pre>
          </div>

          <div class="motion-action-row">
            <Button
              label="記録データを再生"
              icon="pi pi-play"
              :disabled="!teachingRows.length || teachingRecording || store.loading"
              @click="playTeachingRows"
            />
            <Button
              label="記録データをクリア"
              icon="pi pi-trash"
              severity="secondary"
              :disabled="!teachingRows.length"
              @click="clearTeaching"
            />
          </div>
        </template>
      </Card>
    </div>
  </section>
</template>
