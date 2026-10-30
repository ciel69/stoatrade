<script setup lang="ts">
import { computed } from 'vue'
import { useTradingStore, type NearPlan } from '@/entities/trading'
import { UiChip, UiMeter } from '@/shared/ui'
import { countdown, fixed, useNow } from '@/shared/lib'

/** Планы, которые ждут входа: ближайшие к триггеру сверху. Шкала показывает близость цены к условию входа (заполнена полностью, когда цена у уровня). */
const trading = useTradingStore()
const now = useNow()
const block = computed(() => trading.snapshot?.plans ?? null)
const NEAR = 0.3        // % до триггера, с которого план считается «у входа» (как на доске планов)
const FAR = 3           // % и дальше шкала пуста

const counts = computed(() => {
  const c = block.value?.counts
  return c ? [{ id: 'waiting', label: 'ждёт входа', n: c.waiting }, { id: 'watching', label: 'далеко от входа', n: c.watching }, { id: 'in_trade', label: 'в сделке', n: c.in_trade }].filter((x) => x.n > 0) : []
})
const active = computed(() => (block.value ? block.value.counts.waiting + block.value.counts.watching + block.value.counts.in_trade : 0))      // закрытые за сутки в «в работе» не входят
const fill = (p: NearPlan) => (p.dist_pct == null ? 0 : 1 - Math.min(Math.max(p.dist_pct, 0), FAR) / FAR)
const level = (p: NearPlan) => (p.dist_pct != null && p.dist_pct <= NEAR ? 'ok' : 'off')
const eta = (p: NearPlan) => (p.eta_at != null && p.eta_at > now.value ? countdown(p.eta_at - now.value) : null)
const blocked = (p: NearPlan) => (p.blocked_why ? `вход заблокирован: ${p.blocked_why}` : null)
</script>

<template>
  <section class="plans" aria-label="Планы в работе">
    <h2 class="title">Планы в работе<span v-if="block" class="n">{{ active }}</span></h2>
    <div v-if="counts.length" class="counts">
      <UiChip v-for="c in counts" :key="c.id" :tone="c.id === 'waiting' ? 'accent' : 'neutral'">{{ c.label }} {{ c.n }}</UiChip>
    </div>

    <ul v-if="block?.near.length" class="list">
      <li v-for="p in block.near" :key="p.id" class="row">
        <div class="top">
          <b class="sym">{{ p.sym }}</b>
          <UiChip>{{ p.side === 'long' ? 'лонг' : p.side === 'short' ? 'шорт' : '?' }}</UiChip>
          <UiChip v-if="p.mode === 'paper'">paper</UiChip>
          <span class="dist" :class="{ near: p.dist_pct != null && p.dist_pct <= NEAR }">{{ p.dist_pct != null ? fixed(p.dist_pct) + '%' : '–' }}</span>
        </div>
        <p class="text">{{ p.text || 'условие входа не описано' }}</p>
        <UiMeter :fill="fill(p)" :level="level(p)" :label="`${p.sym}: близость к входу`" />
        <div class="meta">
          <span v-if="blocked(p)" class="warn">{{ blocked(p) }}</span>
          <span v-else-if="p.entry_state === 'resting'">лимит уже стоит</span>
          <span v-if="eta(p)" class="eta">свеча {{ eta(p) }}</span>
        </div>
      </li>
    </ul>
    <p v-else-if="block" class="empty">{{ active ? 'Ждущих входа планов нет.' : 'Активных планов нет.' }}</p>
    <div v-else class="skel" aria-busy="true" />
  </section>
</template>

<style scoped>
.plans { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: var(--space-4); min-width: 0; }
.title { margin: 0; font-size: 14px; font-weight: 600; display: flex; align-items: baseline; gap: var(--space-2); }
.n { font-size: 12px; font-weight: 400; color: var(--muted); }
.counts { display: flex; flex-wrap: wrap; gap: var(--space-2); margin-top: var(--space-2); }
.list { list-style: none; margin: var(--space-3) 0 0; padding: 0; }
.row { padding: var(--space-3) 0; border-top: 1px solid var(--line); display: grid; gap: 6px; }
.row:first-child { border-top: 0; padding-top: 0; }
.top { display: flex; align-items: center; gap: var(--space-2); }
.sym { font-size: 15px; letter-spacing: -0.01em; }
.dist { margin-left: auto; font-size: 15px; font-weight: 700; font-variant-numeric: tabular-nums; color: var(--muted); }
.dist.near { color: var(--pos); }
.text { margin: 0; font-size: 12px; color: var(--muted); display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; overflow-wrap: anywhere; }
.meta { display: flex; gap: var(--space-3); font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.meta:empty { display: none; }
.warn { color: var(--warn); }
.eta { margin-left: auto; }
.empty { margin: var(--space-3) 0 0; font-size: 13px; color: var(--muted); }
.skel { height: 64px; margin-top: var(--space-3); border-radius: var(--radius-sm); background: var(--panel2); }
</style>
