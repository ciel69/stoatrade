<script setup lang="ts">
import { computed } from 'vue'
import { planWait, useTradingStore, type NearPlan } from '@/entities/trading'
import { UiChip, UiMeter } from '@/shared/ui'
import { fixed, price, useNow } from '@/shared/lib'

/**
 * Планы, которые ждут входа: ближайшие к триггеру сверху. У каждого плана сказано словами, чего он ждёт (первое невыполненное условие), что ещё не выполнено,
 * когда ближайшая проверка (закрытие свечи с обратным отсчётом или непрерывно по цене) и когда план истечёт. Шкала показывает близость цены к уровню.
 */
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
const rows = computed(() => (block.value?.near ?? []).map((p) => ({ p, w: planWait(p, now.value) })))
const fill = (p: NearPlan) => (p.dist_pct == null ? 0 : 1 - Math.min(Math.max(p.dist_pct, 0), FAR) / FAR)
const level = (p: NearPlan) => (p.dist_pct != null && p.dist_pct <= NEAR ? 'ok' : 'off')
const foot = (p: NearPlan): string[] => [
  p.stop != null ? `стоп ${price(p.stop)}` : '',
  p.target != null ? `цель ${price(p.target)}` : '',
  p.net_rr != null ? `RR ${fixed(p.net_rr, 1)}` : '',
  p.risk != null ? `риск ${fixed(p.risk, 2)} USDT` : '',
].filter(Boolean)
</script>

<template>
  <section class="plans" aria-label="Планы в работе">
    <h2 class="title">Планы в работе<span v-if="block" class="n">{{ active }}</span></h2>
    <div v-if="counts.length" class="counts">
      <UiChip v-for="c in counts" :key="c.id" :tone="c.id === 'waiting' ? 'accent' : 'neutral'">{{ c.label }} {{ c.n }}</UiChip>
    </div>

    <ul v-if="rows.length" class="list">
      <li v-for="{ p, w } in rows" :key="p.id" class="row">
        <div class="top">
          <b class="sym">{{ p.sym }}</b>
          <UiChip>{{ p.side === 'long' ? 'лонг' : p.side === 'short' ? 'шорт' : '?' }}</UiChip>
          <UiChip v-if="p.setup">{{ p.setup }}</UiChip>
          <UiChip v-if="p.mode === 'paper'">paper</UiChip>
          <span class="dist" :class="{ near: p.dist_pct != null && p.dist_pct <= NEAR }" :title="p.dist_pct != null ? 'до уровня условия' : undefined">{{ p.dist_pct != null ? fixed(p.dist_pct) + '%' : '–' }}</span>
        </div>
        <p class="head" :class="`t-${w.tone}`">{{ w.headline }}</p>
        <ul v-if="w.rest.length" class="rest">
          <li v-for="(r, i) in w.rest" :key="i">{{ r }}</li>
        </ul>
        <template v-if="p.level != null">
          <UiMeter :fill="fill(p)" :level="level(p)" :label="`${p.sym}: близость к входу`" />
          <div class="scale"><span>цена {{ price(p.price) }}</span><span>уровень {{ price(p.level) }}</span></div>
        </template>
        <p class="when">{{ w.when }}<span v-if="w.progress" class="prog"> · {{ w.progress }}</span></p>
        <p v-if="foot(p).length" class="foot">{{ foot(p).join(' · ') }}</p>
      </li>
    </ul>
    <p v-else-if="block" class="empty">{{ active ? 'Планов, которые ждут входа, нет.' : 'Активных планов нет.' }}</p>
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
.top { display: flex; align-items: center; flex-wrap: wrap; gap: var(--space-2); }
.sym { font-size: 15px; letter-spacing: -0.01em; }
.dist { margin-left: auto; font-size: 15px; font-weight: 700; font-variant-numeric: tabular-nums; color: var(--muted); }
.dist.near { color: var(--pos); }
.head { margin: 0; font-size: 13px; line-height: 1.4; color: var(--text); overflow-wrap: anywhere; }
.head.t-blocked { color: var(--warn); }
.head.t-paused { color: var(--muted); }
.rest { margin: 0; padding: 0 0 0 16px; font-size: 12px; color: var(--muted); }
.rest li { overflow-wrap: anywhere; }
.scale { display: flex; justify-content: space-between; gap: var(--space-3); font-size: 11px; color: var(--muted); font-variant-numeric: tabular-nums; }
.when { margin: 0; font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.prog { color: var(--text); }
.foot { margin: 0; font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.empty { margin: var(--space-3) 0 0; font-size: 13px; color: var(--muted); }
.skel { height: 64px; margin-top: var(--space-3); border-radius: var(--radius-sm); background: var(--panel2); }
</style>
