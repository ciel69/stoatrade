<script setup lang="ts">
import { computed } from 'vue'
import { UiChip, type ChipTone } from '@/shared/ui'
import { ageText } from '@/shared/lib'
import type { DecisionRow } from '../model/types'

const props = defineProps<{ row: DecisionRow; now: number }>()

const isWhy = computed(() => props.row.what === 'brain_why')
const tone = computed<ChipTone>(() => (props.row.ok === false ? 'bad' : isWhy.value ? 'accent' : 'neutral'))
/** Что произошло: решение/инструмент и код результата. Текст пояснения идёт отдельной строкой и выводится как текст (никакого HTML). */
const head = computed(() => {
  const r = props.row
  if (isWhy.value) return r.decision ? `объяснение · ${r.decision}` : 'объяснение'
  return r.decision ?? r.code ?? 'решение'
})
</script>

<template>
  <li class="item">
    <div class="line">
      <UiChip :tone="tone">{{ head }}</UiChip>
      <span v-if="row.sym" class="sym">{{ row.sym }}</span>
      <span v-else-if="row.plan" class="sym">{{ row.plan }}</span>
      <span class="meta">{{ row.role ?? '' }}<template v-if="row.profile"> · {{ row.profile }}</template></span>
      <span class="age">{{ ageText(row.t, now) }}</span>
    </div>
    <p v-if="row.text" class="text">{{ row.text }}</p>
  </li>
</template>

<style scoped>
.item { padding: var(--space-2) 0; border-top: 1px solid var(--line); }
.line { display: flex; align-items: center; gap: var(--space-2); flex-wrap: wrap; font-size: 12px; }
.sym { font-weight: 700; font-size: 13px; }
.meta { color: var(--muted); }
.age { margin-left: auto; color: var(--muted); font-variant-numeric: tabular-nums; }
.text { margin: var(--space-1) 0 0; font-size: 13px; line-height: 1.4; overflow-wrap: anywhere; }
</style>
