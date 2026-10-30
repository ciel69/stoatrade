<script setup lang="ts">
import { computed, ref } from 'vue'
import { UiChip } from '@/shared/ui'
import { duration, fixed, price, signed } from '@/shared/lib'
import type { TradeCard } from '../model/types'

/** Закрытая сделка: результат к марже крупно, шкала результата в рисках, путь вход → выход; по кнопке «Разбор» раскрываются детали, теги ошибок, урок и тезис. */
const props = defineProps<{ trade: TradeCard }>()
const open = ref(false)

const win = computed(() => (props.trade.pnl ?? 0) > 0)
const tone = (x: number | null) => ((x ?? 0) > 0 ? 'up' : (x ?? 0) < 0 ? 'down' : '')
const held = computed(() => (props.trade.ts_open != null && props.trade.ts_close != null ? props.trade.ts_close - props.trade.ts_open : null))
const EXIT: Record<string, string> = { sl: 'стоп', tp: 'тейк', manual: 'вручную', time: 'по времени' }
const GOOD = new Set(['good', 'good-process-bad-luck'])
const RULE: Record<string, string> = { yes: 'соблюдены', no: 'нарушены', partial: 'частично' }

const dateFmt = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false })
const dt = (t: number | null) => (t == null ? '–' : dateFmt.format(new Date(t * 1000)))

// Шкала результата в рисках: от −1,2R до +3,2R, заливка идёт от нуля к итогу
const LO = -1.2
const HI = 3.2
const bar = computed(() => {
  const r = props.trade.r
  if (r == null) return null
  const v = ((Math.max(LO, Math.min(HI, r)) - LO) / (HI - LO)) * 100
  const z = ((0 - LO) / (HI - LO)) * 100
  return { z, v, left: Math.min(z, v), width: Math.abs(v - z) }
})

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
    ['Вход → выход', `${price(t.entry)} → ${price(t.exit)}`],
    ['Движение цены', t.move_pct != null ? `${signed(t.move_pct)}%` : '–'],
    ['Время в сделке', duration(held.value)],
    ['Комиссии', `${fixed(t.fees, 3)} USDT${t.funding ? ` · funding ${fixed(t.funding, 3)}` : ''}`],
    ['Плечо × маржа', `${t.lev ? t.lev + '× ' : ''}${fixed(t.margin)} USDT`],
    ['Риск до стопа', `${fixed(t.risk, 3)} USDT`],
    ['Откат / ход в плюс', path.value],
    ['Тип · площадка', `${t.stype ?? '–'} · ${t.venue ?? '–'} · ${t.mode ?? '–'}`],
    ['Открыта', dt(t.ts_open)],
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
  <article class="card" :class="win ? 'is-win' : 'is-loss'">
    <div class="top">
      <span class="sym">{{ trade.sym ?? '?' }}</span>
      <UiChip>{{ trade.side === 'long' ? 'лонг' : trade.side === 'short' ? 'шорт' : '?' }}{{ trade.lev ? ' ' + trade.lev + '×' : '' }}</UiChip>
      <UiChip v-if="trade.mode === 'paper'">paper</UiChip>
      <span class="date">{{ dt(trade.ts_close) }}</span>
    </div>

    <div class="main">
      <div class="big" :class="tone(trade.pct)">{{ signed(trade.pct, 1) }}%</div>
      <div class="side">
        <span class="usd" :class="tone(trade.pnl)">{{ signed(trade.pnl, 3) }} USDT</span>
        <span v-if="trade.r != null" class="r" :class="tone(trade.r)">{{ signed(trade.r) }}R</span>
      </div>
    </div>

    <div v-if="bar" class="rbar" role="img" :aria-label="`Результат ${signed(trade.r)}R, шкала от −1,2R до +3,2R`">
      <i class="fill" :class="(trade.r ?? 0) >= 0 ? 'pos' : 'neg'" :style="{ left: bar.left + '%', width: bar.width + '%' }" />
      <i class="zero" :style="{ left: bar.z + '%' }" />
      <i class="end" :style="{ left: bar.v + '%' }" />
    </div>

    <p class="route">{{ price(trade.entry) }} → {{ price(trade.exit) }} · {{ EXIT[trade.reason ?? ''] ?? trade.reason ?? '–' }}<template v-if="held != null"> · {{ duration(held) }}</template></p>

    <div v-if="trade.err.length" class="chips">
      <UiChip v-for="g in trade.err.slice(0, 3)" :key="g" :tone="GOOD.has(g) ? 'neutral' : 'warn'">{{ g }}</UiChip>
    </div>

    <button type="button" class="toggle" :aria-expanded="open" @click="open = !open">
      <svg viewBox="0 0 12 12" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 4.5L6 7.5L9 4.5" /></svg>
      {{ open ? 'Свернуть' : 'Разбор' }}
    </button>

    <div class="detail" :class="{ on: open }">
      <div class="inner">
        <dl class="kv">
          <template v-for="[k, v] in rows" :key="k"><dt>{{ k }}</dt><dd>{{ v }}</dd></template>
        </dl>
        <div v-if="trade.rule_followed || trade.tags.length || trade.err.length > 3" class="chips">
          <UiChip v-if="trade.rule_followed">правила: {{ RULE[trade.rule_followed] ?? trade.rule_followed }}</UiChip>
          <UiChip v-for="g in trade.err.slice(3)" :key="g" tone="warn">{{ g }}</UiChip>
          <UiChip v-for="g in trade.tags" :key="g">{{ g }}</UiChip>
        </div>
        <p v-if="trade.lesson" class="note"><b>Урок:</b> {{ trade.lesson }}</p>
        <p v-if="trade.thesis" class="note"><b>Тезис:</b> {{ trade.thesis }}</p>
        <p v-if="trade.note" class="note muted">Заметка: {{ trade.note }}</p>
        <p class="id">{{ trade.id }}</p>
      </div>
    </div>
  </article>
</template>

<style scoped>
.card { padding: var(--space-4); background: var(--panel2); border: 1px solid var(--line); border-radius: var(--radius); min-width: 0; }
.card.is-win { background: linear-gradient(160deg, color-mix(in srgb, var(--pos) 9%, var(--panel2)), var(--panel2) 55%); }
.card.is-loss { background: linear-gradient(160deg, color-mix(in srgb, var(--neg) 9%, var(--panel2)), var(--panel2) 55%); }
.top { display: flex; align-items: center; gap: var(--space-2); flex-wrap: wrap; }
.sym { font-size: 15px; font-weight: 700; letter-spacing: -0.01em; }
.date { margin-left: auto; font-size: 12px; color: var(--muted); }
.main { display: flex; align-items: baseline; justify-content: space-between; gap: var(--space-3); margin-top: var(--space-3); }
.big { font-size: 28px; font-weight: 700; letter-spacing: -0.02em; line-height: 1.1; font-variant-numeric: tabular-nums; }
.side { display: flex; flex-direction: column; align-items: flex-end; font-variant-numeric: tabular-nums; }
.usd { font-size: 14px; font-weight: 600; }
.r { font-size: 12px; color: var(--muted); }
.up { color: var(--pos); }
.down { color: var(--neg); }
.rbar { position: relative; height: 8px; margin: var(--space-4) 0 var(--space-3); border-radius: 99px; background: var(--line); }
.rbar .fill { position: absolute; top: 0; bottom: 0; border-radius: 99px; }
.rbar .fill.pos { background: var(--pos); }
.rbar .fill.neg { background: var(--neg); }
.rbar .zero { position: absolute; top: -3px; bottom: -3px; width: 2px; margin-left: -1px; background: var(--muted); border-radius: 1px; }
.rbar .end { position: absolute; top: -3px; width: 14px; height: 14px; margin-left: -7px; border-radius: 50%; background: var(--text); border: 3px solid var(--panel2); box-sizing: border-box; }
.route { margin: 0; font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.chips { display: flex; flex-wrap: wrap; gap: var(--space-1); margin-top: var(--space-2); }
.toggle { margin-top: var(--space-3); display: inline-flex; align-items: center; gap: 6px; padding: 0; background: none; border: 0; color: var(--muted); font-size: 12px; cursor: pointer; }
.toggle svg { transition: transform var(--dur-enter) var(--ease-out); }
.toggle[aria-expanded='true'] svg { transform: rotate(180deg); }
@media (hover: hover) and (pointer: fine) {
  .toggle:hover { color: var(--text); }
}
/* раскрытие разбора: строки сетки 0fr -> 1fr, содержимое не прыгает (редкое действие, поэтому движение допустимо) */
.detail { display: grid; grid-template-rows: 0fr; transition: grid-template-rows var(--dur-enter) var(--ease-out); }
.detail.on { grid-template-rows: 1fr; }
.inner { overflow: hidden; min-height: 0; }
.detail.on .inner { padding-top: var(--space-3); }
.kv { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.4fr); gap: 4px var(--space-3); margin: 0; font-size: 12px; }
.kv dt { color: var(--muted); }
.kv dd { margin: 0; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.note { margin: var(--space-3) 0 0; padding: var(--space-2) var(--space-3); background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius-sm); font-size: 12px; line-height: 1.45; overflow-wrap: anywhere; }
.note.muted { color: var(--muted); }
.id { margin: var(--space-2) 0 0; font-size: 11px; color: var(--muted); font-family: var(--mono, monospace); overflow-wrap: anywhere; }
@media (prefers-reduced-motion: reduce) {
  .detail, .toggle svg { transition: none; }
}
</style>
