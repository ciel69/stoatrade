<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import type { ExpCut } from '@/shared/lib'
import { UiChip, UiDetail } from '@/shared/ui'
import { groupFacts, groupTitle, tradeViews, type ExpRowView } from '../lib/experiment'

/**
 * Содержимое боковой панели группы: итоги группы, оценка «насколько это случайно» и список её последних сделок. Каждая сделка со ссылкой в «Сделки» (там открывается её разбор).
 * Сервер отдаёт до 25 последних сделок группы; если их больше, панель так и говорит.
 */
const props = defineProps<{ cut: ExpCut; view: ExpRowView; minN: number }>()
defineEmits<{ close: [] }>()

const title = computed(() => groupTitle(props.cut, props.view.row.name))
const facts = computed(() => groupFacts(props.view.row))
const trades = computed(() => tradeViews(props.view.row.trades))
const hidden = computed(() => Math.max(0, props.view.row.n - props.view.row.trades.length))
</script>

<template>
  <UiDetail :label="title" @close="$emit('close')">
    <template #title>
      <b class="ttl">{{ title }}</b>
      <UiChip v-if="view.confirmed" tone="ok">подтверждена</UiChip>
      <UiChip v-else-if="view.small" tone="warn">мало данных</UiChip>
      <span v-if="view.mode" class="mode">{{ view.mode }}</span>
    </template>

    <p v-if="view.note" class="note">{{ view.note }}</p>
    <p v-if="view.small" class="few">Сделок {{ view.n }}, а для вывода нужно не меньше {{ minN }}. Пока это только наблюдение: одна-две удачные сделки ничего не доказывают.</p>

    <dl class="kv">
      <div v-for="f in facts" :key="f.k" class="r"><dt>{{ f.k }}</dt><dd :class="f.tone">{{ f.v }}</dd></div>
      <div class="r"><dt>Насколько это случайно</dt><dd :class="view.p.tone">{{ view.p.text }}</dd></div>
    </dl>
    <p class="hint">{{ view.pHint }}</p>

    <section class="blk" aria-label="Сделки группы">
      <h3>Последние сделки</h3>
      <ul v-if="trades.length" class="list">
        <li v-for="(t, i) in trades" :key="t.id ?? i">
          <component :is="t.id ? RouterLink : 'div'" class="trade" :to="t.id ? { path: '/trading/trades', query: { trade: t.id } } : undefined">
            <span class="top"><b>{{ t.title }}</b><span class="r2" :class="t.rTone">{{ t.r }}</span></span>
            <span class="meta"><span v-if="t.when">{{ t.when }}</span><span v-if="t.reason">{{ t.reason }}</span><span>{{ t.pnl }}</span></span>
          </component>
        </li>
      </ul>
      <p v-else class="empty">Список сделок не пришёл.</p>
      <p v-if="hidden" class="more">Показаны последние {{ trades.length }}, ещё {{ hidden }} в этой группе не показаны.</p>
    </section>
  </UiDetail>
</template>

<style scoped>
.ttl { font-size: 16px; letter-spacing: -0.01em; overflow-wrap: anywhere; }
.mode { font-size: 12px; color: var(--muted); }
.note { margin: 0; font-size: 13px; line-height: 1.55; text-wrap: pretty; }
.few { margin: 0; padding: var(--space-3); border: 1px solid color-mix(in srgb, var(--warn) 55%, var(--line)); border-radius: var(--radius-sm); font-size: 13px; line-height: 1.5; text-wrap: pretty; }
.kv { margin: 0; display: grid; }
.r { display: flex; align-items: baseline; justify-content: space-between; gap: var(--space-3); padding: var(--space-2) 0; border-top: 1px solid var(--line); font-size: 13px; }
.r:first-child { border-top: 0; }
dt { color: var(--muted); }
dd { margin: 0; font-weight: 600; text-align: right; font-variant-numeric: tabular-nums; }
dd.pos, dd.ok { color: var(--pos); }
dd.neg { color: var(--neg); }
dd.warn { color: var(--warn); }
.hint { margin: calc(-1 * var(--space-2)) 0 0; font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.blk { display: grid; gap: var(--space-2); }
.blk h3 { margin: 0; font-size: 13px; font-weight: 600; }
.list { list-style: none; margin: 0; padding: 0; display: grid; }
.list li { border-top: 1px solid var(--line); }
.list li:first-child { border-top: 0; }
.trade { display: grid; gap: 2px; padding: var(--space-2); margin: 0 calc(-1 * var(--space-2)); border-radius: var(--radius-sm); color: inherit; text-decoration: none; font-size: 13px; transition: background-color var(--dur-fast) ease; }
a.trade:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.top { display: flex; align-items: baseline; justify-content: space-between; gap: var(--space-3); }
.r2 { font-weight: 600; font-variant-numeric: tabular-nums; }
.r2.pos { color: var(--pos); }
.r2.neg { color: var(--neg); }
.meta { display: flex; flex-wrap: wrap; gap: 0 var(--space-3); font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.empty, .more { margin: 0; font-size: 12px; line-height: 1.5; color: var(--muted); }
@media (hover: hover) and (pointer: fine) {
  a.trade:hover { background: var(--panel2); }
}
@media (prefers-reduced-motion: reduce) {
  .trade { transition: none; }
}
</style>
