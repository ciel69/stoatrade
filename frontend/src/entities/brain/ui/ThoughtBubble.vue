<script setup lang="ts">
import { computed } from 'vue'
import { ageText } from '@/shared/lib'
import { thoughtHint, thoughtKind, thoughtText } from '../lib/human'
import type { Thought } from '../lib/thoughts'

const props = defineProps<{ thought: Thought; now: number }>()
const row = computed(() => props.thought.row)
const kind = computed(() => thoughtKind(row.value))
const text = computed(() => thoughtText(row.value.text))
const hint = computed(() => thoughtHint(row.value, props.now))
</script>

<template>
  <li class="thought" tabindex="0" :title="hint">
    <div class="line">
      <b v-if="row.sym" class="sym">{{ row.sym }}</b>
      <span class="kind">{{ kind }}</span>
      <span class="age">{{ ageText(row.t, now) }}</span>
    </div>
    <p class="text">{{ text }}</p>
  </li>
</template>

<style scoped>
.thought {
  height: var(--thought-h);
  box-sizing: border-box;
  padding: var(--space-2) var(--space-3);
  background: var(--panel2);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  overflow: hidden;
  outline: none;
  transition: border-color var(--dur-fast) ease;
}
.thought:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
@media (hover: hover) and (pointer: fine) {
  .thought:hover { border-color: var(--line2); }
}
.line { display: flex; align-items: baseline; gap: var(--space-2); font-size: 12px; color: var(--muted); }
.sym { color: var(--text); font-size: 13px; }
.kind { color: var(--zone-brain); }
.age { margin-left: auto; font-variant-numeric: tabular-nums; white-space: nowrap; }
.text { margin: 2px 0 0; font-size: 13px; line-height: 1.4; overflow-wrap: anywhere; display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
@media (prefers-reduced-motion: reduce) {
  .thought { transition: none; }
}
</style>
