<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';

import Button from 'primevue/button';
import Card from 'primevue/card';
import InputText from 'primevue/inputtext';
import Tag from 'primevue/tag';
import Textarea from 'primevue/textarea';
import { useToast } from 'primevue/usetoast';

import { useControlStore } from '@/stores/control';

const store = useControlStore();
const toast = useToast();

const experimentType = ref('walk-tuning');
const experimentName = ref('');
const noteText = ref('');
const busy = ref(false);

const running = computed(() => store.experimentRunning);
const recentExperiments = computed(() => store.experiments.slice(0, 8));

onMounted(() => {
  void store.refreshExperiments().catch(() => undefined);
});

async function run(action: () => Promise<void>, failure: string): Promise<void> {
  busy.value = true;
  try {
    await action();
  } catch (error) {
    toast.add({
      severity: 'error',
      summary: failure,
      detail: error instanceof Error ? error.message : undefined,
      life: 3500,
    });
  } finally {
    busy.value = false;
  }
}

function start(): void {
  if (!experimentType.value.trim()) return;
  void run(
    () => store.beginExperiment(experimentType.value.trim(), experimentName.value.trim() || undefined),
    '実験を開始できません',
  );
}

function stop(): void {
  void run(() => store.endExperiment(), '実験を停止できません');
}

function addNote(): void {
  const text = noteText.value.trim();
  if (!text) return;
  void run(async () => {
    await store.noteExperiment(text);
    noteText.value = '';
    toast.add({ severity: 'success', summary: 'ノートを追記しました', life: 1500 });
  }, 'ノートを追記できません');
}
</script>

<template>
  <Card class="motion-card experiment-card">
    <template #title>実験記録</template>
    <template #subtitle>
      manifest / telemetry.csv / events.jsonl / notes.md を実行ごとのディレクトリに記録します。
      サイクル指定歩行（1周期・3周期）は自動で記録されます。
    </template>
    <template #content>
      <div class="experiment-controls">
        <label class="field compact-field">
          <span>タイプ</span>
          <InputText v-model="experimentType" :disabled="running !== null" placeholder="walk-tuning" />
        </label>
        <label class="field compact-field">
          <span>名前（任意）</span>
          <InputText v-model="experimentName" :disabled="running !== null" />
        </label>
        <Button
          v-if="!running"
          label="記録開始"
          icon="pi pi-circle-fill"
          severity="danger"
          :disabled="busy || !experimentType.trim()"
          @click="start"
        />
        <Button
          v-else
          label="記録停止"
          icon="pi pi-stop-circle"
          severity="secondary"
          :disabled="busy"
          @click="stop"
        />
        <Tag
          :severity="running ? 'danger' : 'secondary'"
          :value="running ? `記録中: ${running.experiment_id}` : '待機中'"
        />
      </div>

      <div v-if="running" class="experiment-note-row">
        <Textarea v-model="noteText" rows="2" auto-resize placeholder="観察メモ（notes.md に追記）" />
        <Button label="ノート追記" size="small" severity="secondary" :disabled="busy || !noteText.trim()" @click="addNote" />
      </div>

      <p v-if="store.lastExperimentSummary" class="motion-helper">
        直前のラン: {{ store.lastExperimentSummary.manifest.experiment_id }}
        （{{ store.lastExperimentSummary.telemetry_rows }}行 /
        {{ store.lastExperimentSummary.duration_sec.toFixed(1) }}s）
        → {{ store.lastExperimentSummary.directory }}
        <br />
        解析: <code>python scripts/walk_metrics.py metrics "{{ store.lastExperimentSummary.directory }}"</code>
      </p>

      <table v-if="recentExperiments.length" class="experiment-table">
        <thead>
          <tr>
            <th>実験ID</th>
            <th>タイプ</th>
            <th>開始</th>
            <th>行数</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in recentExperiments" :key="item.experiment_id">
            <td>{{ item.experiment_id }}</td>
            <td>{{ item.experiment_type }}</td>
            <td>{{ item.started_at }}</td>
            <td>{{ item.row_counts?.telemetry_rows ?? '-' }}</td>
          </tr>
        </tbody>
      </table>
      <p v-else class="motion-helper">まだ実験記録はありません。</p>
    </template>
  </Card>
</template>
