<script setup lang="ts">
import Card from 'primevue/card';

defineProps<{
  title: string;
  lead: string;
  stage: string;
  bullets: string[];
}>();
</script>

<template>
  <Card class="placeholder-card">
    <template #title>{{ title }}</template>
    <template #subtitle>{{ lead }}</template>
    <template #content>
      <p class="placeholder-stage">現在の段階: {{ stage }}</p>
      <ul class="placeholder-list">
        <li v-for="bullet in bullets" :key="bullet">{{ bullet }}</li>
      </ul>
    </template>
  </Card>
</template>
