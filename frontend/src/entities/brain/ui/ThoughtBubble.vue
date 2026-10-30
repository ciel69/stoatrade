<script setup lang="ts">
import { computed } from 'vue'
import { ageText } from '@/shared/lib'
import type { Thought } from '../lib/thoughts'

const props = defineProps<{ thought: Thought; now: number }>()
const row = computed(() => props.thought.row)
const kind = computed(() => (row.value.what === 'brain_why' ? 'объяснил' : row.value.decision ? decisionText(row.value.decision) : row.value.ok === false ? 'отказ' : 'разобрал'))

function decisionText(d: string): string {
  const t: Record<string, string> = { move_stop: 'подтянул стоп', close: 'закрыл', partial_close: 'закрыл часть', cancel: 'отменил вход', amend: 'поправил план', escalate: 'позвал владельца', hold: 'держит' }
  return t[d] ?? d
}
</script>

<template>
  <li class="thought" :style="{ opacity: thought.opacity }" tabindex="0">
    <div class="line">
      <b v-if="row.sym" class="sym">{{ row.sym }}</b>
      <span class="kind">{{ kind }}</span>
      <span class="age">{{ ageText(row.t, now) }}</span>
    </div>
    <p class="text">{{ row.text }}</p>
  </li>
</template>

<style scoped>
.thought {
  padding: var(--space-2) var(--space-3);
  background: var(--panel2);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  /* растворение: прозрачность идёт от возраста мысли, переход гасит скачок между обновлениями (константное движение: linear) */
  transition: opacity 1s linear;
}
.thought:focus-visible { opacity: 1 !important; }
.line { display: flex; align-items: baseline; gap: var(--space-2); font-size: 12px; color: var(--muted); }
.sym { color: var(--text); font-size: 13px; }
.kind { color: var(--zone-brain); }
.age { margin-left: auto; font-variant-numeric: tabular-nums; }
.text { margin: 2px 0 0; font-size: 13px; line-height: 1.4; overflow-wrap: anywhere; display: -webkit-box; -webkit-line-clamp: 3; line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
@media (prefers-reduced-motion: reduce) {
  .thought { transition: none; }
}
</style>
