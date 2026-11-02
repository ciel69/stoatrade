<script setup lang="ts">
defineProps<{ label: string; value: string; sub?: string; tone?: 'pos' | 'neg' | 'warn' }>()
</script>

<template>
  <div class="tile">
    <div class="label">{{ label }}</div>
    <div class="value" :class="tone">{{ value }}</div>
    <div v-if="sub" class="sub">{{ sub }}</div>
  </div>
</template>

<style scoped>
.tile {
  background: var(--panel2);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  padding: var(--space-3);
  min-width: 0;
}
.label { font-size: 12px; color: var(--muted); }
.value { font-size: 22px; font-weight: 700; letter-spacing: -0.02em; line-height: 1.2; overflow-wrap: anywhere; }
.value.pos { color: var(--pos); }
.value.neg { color: var(--neg); }
.value.warn { color: var(--warn); }
.sub { margin-top: 2px; font-size: 12px; color: var(--muted); }
</style>
