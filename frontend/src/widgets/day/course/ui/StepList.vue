<script setup lang="ts">
import type { StepRow } from '@/entities/day'
import { UiStatusDot } from '@/shared/ui'

/** Список шагов дня: значок состояния (готово, замечание, сбой, идёт), название, что шаг делает, причина замечания или сбоя, длительность. */
defineProps<{ rows: StepRow[] }>()
</script>

<template>
  <ol class="steps">
    <li v-for="r in rows" :key="r.key" class="st" :class="`s-${r.state}`">
      <span class="mk" aria-hidden="true">
        <UiStatusDot v-if="r.state === 'run'" state="busy" />
        <svg v-else-if="r.state === 'done'" viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 8.5l3 3 6-7" /></svg>
        <svg v-else-if="r.state === 'amber'" viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M8 3.5v5.5M8 12.2v.1" /></svg>
        <svg v-else viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M4 4l8 8M12 4l-8 8" /></svg>
      </span>
      <div class="nm">
        <b>{{ r.label }}</b>
        <span class="does">{{ r.does }}</span>
        <span v-if="r.note" class="note">{{ r.note }}</span>
      </div>
      <span class="stat"><span class="sr">Состояние: </span>{{ r.status }}<template v-if="r.dur"> · {{ r.dur }}</template></span>
    </li>
  </ol>
</template>

<style scoped>
.steps { list-style: none; margin: 0; padding: 0; display: grid; }
.st { display: grid; grid-template-columns: 20px minmax(0, 1fr) auto; gap: var(--space-3); align-items: start; padding: var(--space-2) 0; border-top: 1px solid var(--line); }
.mk { display: grid; place-items: center; height: 20px; color: var(--pos); }
.s-amber .mk { color: var(--warn); }
.s-fail .mk { color: var(--neg); }
.nm { display: grid; gap: 2px; min-width: 0; font-size: 13px; }
.nm b { font-weight: 600; }
.does { color: var(--muted); font-size: 12px; line-height: 1.5; text-wrap: pretty; }
.note { font-size: 12px; line-height: 1.5; color: var(--warn); text-wrap: pretty; }
.s-fail .note { color: var(--neg); }
.stat { font-size: 12px; color: var(--muted); white-space: nowrap; font-variant-numeric: tabular-nums; padding-top: 2px; }
.s-fail .stat { color: var(--neg); }
.s-amber .stat { color: var(--warn); }
.sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
@media (max-width: 520px) {
  .st { grid-template-columns: 20px minmax(0, 1fr); }
  .stat { grid-column: 2; padding-top: 0; white-space: normal; }
}
</style>
