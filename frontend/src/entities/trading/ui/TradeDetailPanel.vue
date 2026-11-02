<script setup lang="ts">
import { computed } from 'vue'
import { UiChip, UiDetail } from '@/shared/ui'
import { duration, fixed, price, signed } from '@/shared/lib'
import type { TradeCard } from '../model/types'

/** Боковая панель разбора закрытой сделки: итог, параметры, путь цены (откат и ход в плюс), теги ошибок, урок, тезис и заметка. */
const props = defineProps<{ trade: TradeCard }>()
defineEmits<{ close: [] }>()

const tone = (x: number | null) => ((x ?? 0) > 0 ? 'up' : (x ?? 0) < 0 ? 'down' : '')
const held = computed(() => (props.trade.ts_open != null && props.trade.ts_close != null ? props.trade.ts_close - props.trade.ts_open : null))
const GOOD = new Set(['good', 'good-process-bad-luck'])
const RULE: Record<string, string> = { yes: 'соблюдены', no: 'нарушены', partial: 'частично' }
const EXIT: Record<string, string> = { sl: 'стоп', tp: 'тейк', manual: 'вручную', time: 'по времени' }
const dateFmt = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false })
const dt = (t: number | null) => (t == null ? '–' : dateFmt.format(new Date(t * 1000)))

const path = computed(() => {
  const p = props.trade.path
  if (!p || p.mae_pct == null || p.mfe_pct == null) return '–'
  let s = `${p.mae_pct > 0 ? '−' : ''}${fixed(p.mae_pct)}% / ${p.mfe_pct > 0 ? '+' : ''}${fixed(p.mfe_pct)}%`
  if (p.mae_r != null && p.mfe_r != null) s += ` (${fixed(p.mae_r)}R / ${fixed(p.mfe_r)}R)`
  return s
})
const rows = computed<Array<[string, string]>>(() => {
  const t = props.trade
  const out: Array<[string, string]> = [
    ['Вход → выход', `${price(t.entry)} → ${price(t.exit)} · ${EXIT[t.reason ?? ''] ?? t.reason ?? '–'}`],
    ['Движение цены', t.move_pct != null ? `${signed(t.move_pct)}%` : '–'],
    ['Время в сделке', duration(held.value)],
    ['Комиссии', `${fixed(t.fees, 3)} USDT${t.funding ? ` · funding ${fixed(t.funding, 3)}` : ''}`],
    ['Плечо × маржа', `${t.lev ? t.lev + '× ' : ''}${fixed(t.margin)} USDT`],
    ['Риск до стопа', `${fixed(t.risk, 3)} USDT`],
    ['Откат / ход в плюс', path.value],
    ['Тип · площадка', `${t.stype ?? '–'} · ${t.venue ?? '–'} · ${t.mode ?? '–'}`],
    ['Открыта', dt(t.ts_open)],
    ['Закрыта', dt(t.ts_close)],
  ]
  if (t.sl0 != null && t.sl != null && t.sl0 !== t.sl) out.push(['Стоп: начальный → итоговый', `${price(t.sl0)} → ${price(t.sl)}`])
  if (t.adds || t.parts) out.push(['Добор · частичные закрытия', `${t.adds} · ${t.parts}`])
  if (t.plan_id) out.push(['План', t.plan_id])
  const exp = [t.exp, t.var, t.setup, t.hyp].filter(Boolean).join(' / ')
  if (exp) out.push(['Эксперимент', exp])
  return out
})
</script>

<template>
  <UiDetail :label="`Сделка ${trade.sym ?? ''}`" @close="$emit('close')">
    <template #title>
      <b class="sym">{{ trade.sym ?? '?' }}</b>
      <UiChip>{{ trade.side === 'long' ? 'лонг' : trade.side === 'short' ? 'шорт' : '?' }}{{ trade.lev ? ' ' + trade.lev + '×' : '' }}</UiChip>
      <UiChip v-if="trade.mode === 'paper'">paper</UiChip>
    </template>

    <div class="res">
      <span class="big" :class="tone(trade.pct)">{{ signed(trade.pct, 1) }}%</span>
      <span class="usd" :class="tone(trade.pnl)">{{ signed(trade.pnl, 3) }} USDT</span>
      <span v-if="trade.r != null" class="r" :class="tone(trade.r)">{{ signed(trade.r) }}R</span>
    </div>

    <div v-if="trade.err.length || trade.rule_followed || trade.tags.length" class="chips">
      <UiChip v-if="trade.rule_followed">правила: {{ RULE[trade.rule_followed] ?? trade.rule_followed }}</UiChip>
      <UiChip v-for="g in trade.err" :key="g" :tone="GOOD.has(g) ? 'neutral' : 'warn'">{{ g }}</UiChip>
      <UiChip v-for="g in trade.tags" :key="g">{{ g }}</UiChip>
    </div>

    <dl class="kv"><template v-for="[k, v] in rows" :key="k"><dt>{{ k }}</dt><dd>{{ v }}</dd></template></dl>

    <p v-if="trade.lesson" class="note"><b>Урок:</b> {{ trade.lesson }}</p>
    <p v-if="trade.thesis" class="note"><b>Тезис:</b> {{ trade.thesis }}</p>
    <p v-if="trade.note" class="note muted">Заметка: {{ trade.note }}</p>
    <p class="id">{{ trade.id }}</p>
  </UiDetail>
</template>

<style scoped>
.sym { font-size: 18px; letter-spacing: -0.01em; }
.res { display: flex; align-items: baseline; gap: var(--space-3); font-variant-numeric: tabular-nums; }
.big { font-size: 30px; font-weight: 700; letter-spacing: -0.02em; line-height: 1.1; }
.usd { font-size: 14px; font-weight: 600; }
.r { font-size: 13px; color: var(--muted); }
.up { color: var(--pos); }
.down { color: var(--neg); }
.chips { display: flex; flex-wrap: wrap; gap: var(--space-1); }
.kv { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.4fr); gap: 5px var(--space-3); margin: 0; font-size: 12px; }
.kv dt { color: var(--muted); }
.kv dd { margin: 0; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.note { margin: 0; padding: var(--space-2) var(--space-3); background: var(--panel2); border: 1px solid var(--line); border-radius: var(--radius-sm); font-size: 12px; line-height: 1.45; overflow-wrap: anywhere; }
.note.muted { color: var(--muted); }
.id { margin: 0; font-size: 11px; color: var(--muted); font-family: var(--mono, monospace); overflow-wrap: anywhere; }
</style>
