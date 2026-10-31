<script setup lang="ts">
import { computed } from 'vue'
import { UiChip } from '@/shared/ui'
import { duration, price, signed } from '@/shared/lib'
import type { TradeCard } from '../model/types'

/** Закрытая сделка: результат к марже крупно, шкала результата в рисках, путь вход → выход; по кнопке «Разбор» детали открываются боковой панелью (теги ошибок, урок, тезис, параметры). */
const props = defineProps<{ trade: TradeCard; selected?: boolean }>()
const emit = defineEmits<{ select: [] }>()

const win = computed(() => (props.trade.pnl ?? 0) > 0)
const tone = (x: number | null) => ((x ?? 0) > 0 ? 'up' : (x ?? 0) < 0 ? 'down' : '')
const held = computed(() => (props.trade.ts_open != null && props.trade.ts_close != null ? props.trade.ts_close - props.trade.ts_open : null))
const EXIT: Record<string, string> = { sl: 'стоп', tp: 'тейк', manual: 'вручную', time: 'по времени' }
const GOOD = new Set(['good', 'good-process-bad-luck'])

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
</script>

<template>
  <article class="card" :class="[win ? 'is-win' : 'is-loss', { sel: selected }]">
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

    <button type="button" class="toggle" :aria-pressed="selected" @click="emit('select')">
      Разбор
      <svg viewBox="0 0 12 12" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4.5 3L7.5 6L4.5 9" /></svg>
    </button>
  </article>
</template>

<style scoped>
.card { padding: var(--space-4); background: var(--panel2); border: 1px solid var(--line); border-radius: var(--radius); min-width: 0; transition: border-color var(--dur-fast) ease; }
.card.is-win { background: linear-gradient(160deg, color-mix(in srgb, var(--pos) 9%, var(--panel2)), var(--panel2) 55%); }
.card.is-loss { background: linear-gradient(160deg, color-mix(in srgb, var(--neg) 9%, var(--panel2)), var(--panel2) 55%); }
.card.sel { border-color: var(--accent); }
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
.toggle:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
@media (hover: hover) and (pointer: fine) {
  .toggle:hover { color: var(--text); }
}
</style>
