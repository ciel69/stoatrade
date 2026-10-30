<script setup lang="ts">
import { computed } from 'vue'
import { useTradingStore, type LimitRow } from '@/entities/trading'
import { UiChip, UiMeter } from '@/shared/ui'
import { fixed } from '@/shared/lib'

const SHORT: Record<string, string> = { session: 'бюджет сессии', slots: 'слоты', stops: 'стопы подряд', trades: 'сделки', learn_loss: 'учёба: убыток', learn_trades: 'учёба: сделки' }
const trading = useTradingStore()
/** Дневной лимит уже в полосе денег; здесь остальные запасы. */
const rows = computed(() => (trading.snapshot?.limits ?? []).filter((r) => r.id !== 'daily'))
const paper = computed(() => trading.snapshot?.limits_paper ?? false)

function text(r: LimitRow): string {
  if (r.id === 'stops') return `${fixed(r.used, 0)} / ${fixed(r.limit, 0)}`
  return r.unit === 'USDT' ? `${fixed(r.room)} из ${fixed(r.limit)}` : `${fixed(r.room, 0)} из ${fixed(r.limit, 0)}`
}
function fill(r: LimitRow): number {
  if (!r.limit) return 0
  return r.id === 'stops' ? 1 - (r.used ?? 0) / r.limit : (r.room ?? 0) / r.limit
}
</script>

<template>
  <section v-if="rows.length" class="limits" aria-label="Запас до лимитов">
    <div v-for="r in rows" :key="r.id" class="lim">
      <div class="top"><span class="name">{{ SHORT[r.id] ?? r.label }}</span><span class="val">{{ text(r) }}</span></div>
      <UiMeter :fill="fill(r)" :level="r.level" :label="SHORT[r.id] ?? r.label" />
    </div>
    <UiChip v-if="paper" class="note">paper: лимиты на сделки не влияют</UiChip>
  </section>
</template>

<style scoped>
.limits { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-3) var(--space-5); padding: 0 var(--space-1); }
.lim { flex: 1 1 140px; max-width: 220px; display: flex; flex-direction: column; gap: 4px; }
.top { display: flex; justify-content: space-between; gap: var(--space-2); font-size: 12px; color: var(--muted); }
.val { color: var(--text); font-variant-numeric: tabular-nums; }
.note { margin-left: auto; }
</style>
