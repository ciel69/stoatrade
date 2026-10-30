<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { buildSeries, nearest, niceTicks, useTradingStore, type CurveRange } from '@/entities/trading'
import { UiSegment } from '@/shared/ui'
import { fixed, signed } from '@/shared/lib'

/**
 * Накопленный результат закрытых сделок выбранного режима. Форма: линия-ступенька (баланс меняется в момент закрытия сделки, между ними он постоянен), одна серия,
 * поэтому без легенды: название блока говорит, что нарисовано. Ось одна, нулевая линия всегда в кадре. Наведение и фокус: вертикальная линия привязана к ближайшей сделке,
 * в подсказке накопленное значение и сама сделка. Те же числа есть в таблице под графиком.
 */
const trading = useTradingStore()
const range = ref<CurveRange>('all')
const ranges = [{ value: 'day', label: '24 ч' }, { value: 'week', label: '7 дн' }, { value: 'all', label: 'всё' }] as const

const H = 176
const M = { l: 46, r: 14, t: 12, b: 24 }
const wrap = ref<HTMLElement | null>(null)
const W = ref(600)
let ro: ResizeObserver | null = null
onMounted(() => {
  if (!wrap.value) return
  W.value = Math.max(280, Math.round(wrap.value.clientWidth))
  ro = new ResizeObserver(() => wrap.value && (W.value = Math.max(280, Math.round(wrap.value.clientWidth))))
  ro.observe(wrap.value)
})
onUnmounted(() => ro?.disconnect())

const nowS = computed(() => (trading.snapshot ? Date.now() / 1000 : 0))      // пересчёт идёт вместе со снимком: правый край это момент последнего обновления
const series = computed(() => buildSeries(trading.snapshot?.curve, range.value, nowS.value))

const geo = computed(() => {
  const s = series.value
  if (!s) return null
  const vals = [s.start, 0, ...s.points.map((p) => p.cum)]
  let lo = Math.min(...vals)
  let hi = Math.max(...vals)
  const pad = (hi - lo || 1) * 0.08
  lo -= pad
  hi += pad
  const pw = W.value - M.l - M.r
  const ph = H - M.t - M.b
  const x = (t: number) => M.l + ((t - s.t0) / (s.t1 - s.t0 || 1)) * pw
  const y = (v: number) => M.t + (1 - (v - lo) / (hi - lo)) * ph
  let line = `M${x(s.t0).toFixed(1)} ${y(s.start).toFixed(1)}`
  let prev = y(s.start)
  for (const p of s.points) {
    line += `L${x(p.ts).toFixed(1)} ${prev.toFixed(1)}L${x(p.ts).toFixed(1)} ${y(p.cum).toFixed(1)}`
    prev = y(p.cum)
  }
  line += `L${x(s.t1).toFixed(1)} ${prev.toFixed(1)}`
  const zero = y(0)
  const area = `${line}L${x(s.t1).toFixed(1)} ${zero.toFixed(1)}L${x(s.t0).toFixed(1)} ${zero.toFixed(1)}Z`
  return { x, y, line, area, zero, ticks: niceTicks(lo, hi, 4), endX: x(s.t1), endY: prev, pw }
})

const dayFmt = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short' })
const hourFmt = new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit', hour12: false })
const fullFmt = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false })
const edge = (t: number) => (range.value === 'day' ? hourFmt : dayFmt).format(new Date(t * 1000))
const tickText = (v: number) => (Math.abs(v) >= 1000 ? `${(v / 1000).toFixed(1)}K` : Number.isInteger(v) ? String(v) : fixed(v, Math.abs(v) < 1 ? 2 : 1))

const active = ref<number | null>(null)
const pt = computed(() => (series.value && active.value != null ? series.value.points[active.value] ?? null : null))
const tip = computed(() => {
  if (!pt.value || !geo.value) return null
  const x = geo.value.x(pt.value.ts)
  return { x, y: geo.value.y(pt.value.cum), left: Math.min(Math.max(x, 70), W.value - 70) }
})

function onMove(ev: PointerEvent): void {
  const s = series.value
  const g = geo.value
  const el = ev.currentTarget as SVGElement
  if (!s || !g || !s.points.length) return
  const px = ev.clientX - el.getBoundingClientRect().left
  active.value = nearest(s.points, s.t0 + ((px - M.l) / g.pw) * (s.t1 - s.t0))
}
function onKey(ev: KeyboardEvent): void {
  const n = series.value?.points.length ?? 0
  if (!n || (ev.key !== 'ArrowLeft' && ev.key !== 'ArrowRight')) return
  ev.preventDefault()
  active.value = Math.min(n - 1, Math.max(0, (active.value ?? n) + (ev.key === 'ArrowRight' ? 1 : -1)))
}

const tone = computed(() => ((series.value?.delta ?? 0) > 0 ? 'up' : (series.value?.delta ?? 0) < 0 ? 'down' : ''))
const summary = computed(() => (series.value ? `Накопленный результат ${signed(series.value.last)} USDT, за период ${signed(series.value.delta)}, сделок в периоде ${series.value.points.length}` : 'Закрытых сделок нет'))
const rows = computed(() => (series.value ? [...series.value.points].reverse().slice(0, 50) : []))
</script>

<template>
  <section class="chart" aria-label="Накопленный PnL">
    <header class="head">
      <h2 class="title">Накопленный PnL</h2>
      <span v-if="series" class="val" :class="tone">{{ signed(series.delta) }}<small> USDT за период</small></span>
      <UiSegment v-model="range" :options="ranges" label="Период графика" class="seg" />
    </header>

    <div ref="wrap" class="plot" :style="{ height: H + 'px' }">
      <svg v-if="series && geo" :width="W" :height="H" :viewBox="`0 0 ${W} ${H}`" tabindex="0" role="img" :aria-label="summary" @pointermove="onMove" @pointerleave="active = null"
           @focus="active = series.points.length ? series.points.length - 1 : null" @blur="active = null" @keydown="onKey">
        <g class="grid">
          <g v-for="v in geo.ticks" :key="v">
            <line :x1="M.l" :x2="W - M.r" :y1="geo.y(v)" :y2="geo.y(v)" :class="{ zero: v === 0 }" />
            <text :x="M.l - 8" :y="geo.y(v)" class="tick" text-anchor="end" dominant-baseline="middle">{{ tickText(v) }}</text>
          </g>
          <text :x="M.l" :y="H - 6" class="tick" text-anchor="start">{{ edge(series.t0) }}</text>
          <text :x="W - M.r" :y="H - 6" class="tick" text-anchor="end">{{ edge(series.t1) }}</text>
        </g>
        <path class="area" :d="geo.area" />
        <path class="line" :d="geo.line" />
        <circle class="end" :cx="geo.endX" :cy="geo.endY" r="4" />
        <g v-if="tip">
          <line class="cross" :x1="tip.x" :x2="tip.x" :y1="M.t" :y2="H - M.b" />
          <circle class="end" :cx="tip.x" :cy="tip.y" r="4" />
        </g>
      </svg>
      <p v-else class="empty">{{ trading.snapshot ? 'Закрытых сделок в этом режиме ещё нет.' : '' }}</p>

      <div v-if="tip && pt" class="tip" :style="{ left: tip.left + 'px' }">
        <b class="tv">{{ signed(pt.cum) }} <small>USDT</small></b>
        <span class="tr"><i class="key" aria-hidden="true" />{{ pt.sym ?? 'сделка' }} {{ signed(pt.pnl) }}</span>
        <span class="tt">{{ fullFmt.format(new Date(pt.ts * 1000)) }}</span>
      </div>
    </div>

    <details v-if="rows.length" class="tbl">
      <summary>Таблица сделок</summary>
      <div class="scroll">
        <table>
          <thead><tr><th scope="col">Закрыта</th><th scope="col">Монета</th><th scope="col" class="n">Результат</th><th scope="col" class="n">Накоплено</th></tr></thead>
          <tbody>
            <tr v-for="p in rows" :key="p.ts + (p.sym ?? '')">
              <td>{{ fullFmt.format(new Date(p.ts * 1000)) }}</td><td>{{ p.sym }}</td><td class="n">{{ signed(p.pnl) }}</td><td class="n">{{ signed(p.cum) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </details>
  </section>
</template>

<style scoped>
.chart { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: var(--space-4); min-width: 0; }
.head { display: flex; align-items: center; gap: var(--space-3); margin-bottom: var(--space-3); flex-wrap: wrap; }
.title { margin: 0; font-size: 14px; font-weight: 600; }
.val { font-size: 20px; font-weight: 700; letter-spacing: -0.02em; font-variant-numeric: tabular-nums; }
.val small { font-size: 12px; font-weight: 400; color: var(--muted); letter-spacing: 0; }
.val.up { color: var(--pos); }
.val.down { color: var(--neg); }
.seg { margin-left: auto; }
.plot { position: relative; }
svg { display: block; touch-action: pan-y; outline: none; }
svg:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: var(--radius-sm); }
.grid line { stroke: var(--line); stroke-width: 1; }
.grid line.zero { stroke: var(--line2); }
.tick { fill: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }
.line { fill: none; stroke: var(--accent); stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }
.area { fill: var(--accent); opacity: 0.1; }
.end { fill: var(--accent); stroke: var(--panel); stroke-width: 2; }
.cross { stroke: var(--muted); stroke-width: 1; }
.empty { margin: 0; height: 100%; display: grid; place-items: center; color: var(--muted); font-size: 13px; }
.tip { position: absolute; top: 4px; transform: translateX(-50%); pointer-events: none; display: grid; gap: 2px; padding: var(--space-2) var(--space-3); background: var(--panel2); border: 1px solid var(--line2); border-radius: var(--radius-sm); font-size: 12px; white-space: nowrap; }
.tv { font-size: 14px; font-variant-numeric: tabular-nums; }
.tv small { font-weight: 400; color: var(--muted); }
.tr { display: flex; align-items: center; gap: 6px; font-variant-numeric: tabular-nums; }
.key { width: 10px; height: 2px; background: var(--accent); border-radius: 1px; }
.tt { color: var(--muted); }
.tbl { margin-top: var(--space-3); font-size: 12px; color: var(--muted); }
.tbl summary { cursor: pointer; width: fit-content; }
.scroll { max-height: 200px; overflow-y: auto; margin-top: var(--space-2); }
table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
th, td { padding: 4px var(--space-2); text-align: left; border-top: 1px solid var(--line); color: var(--text); font-weight: 400; }
th { color: var(--muted); border-top: 0; position: sticky; top: 0; background: var(--panel); }
.n { text-align: right; }
</style>
