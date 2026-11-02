<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { buildSeries, niceTicks, smoothPath, useTradingStore, type CurveRange } from '@/entities/trading'
import { UiSegment } from '@/shared/ui'
import { signed } from '@/shared/lib'

/**
 * Накопленный результат закрытых сделок выбранного режима, в стиле кривой старого дашборда: по оси X сделки подряд (а не время, поэтому без длинных пустых участков),
 * плавная линия, выше нуля зелёная, ниже красная, заливка гуще дальше от нуля. Одна серия: легенды нет, название блока говорит, что нарисовано.
 * Наведение и фокус: пунктирная линия на ближайшей сделке; те же числа есть в таблице под графиком.
 */
const trading = useTradingStore()
const range = ref<CurveRange>('all')
const ranges = [{ value: 'day', label: '24 ч' }, { value: 'week', label: '7 дн' }, { value: 'all', label: 'всё' }] as const

const M = { l: 42, r: 14, t: 12, b: 22 }
const wrap = ref<HTMLElement | null>(null)
const W = ref(600)
const H = computed(() => (W.value < 420 ? 170 : 200))
let ro: ResizeObserver | null = null
onMounted(() => {
  if (!wrap.value) return
  W.value = Math.max(240, Math.round(wrap.value.clientWidth))
  ro = new ResizeObserver(() => wrap.value && (W.value = Math.max(240, Math.round(wrap.value.clientWidth))))
  ro.observe(wrap.value)
})
onUnmounted(() => ro?.disconnect())

const nowS = computed(() => (trading.snapshot ? Date.now() / 1000 : 0))
const series = computed(() => buildSeries(trading.snapshot?.curve, range.value, nowS.value))
const n = computed(() => series.value?.points.length ?? 0)

const geo = computed(() => {
  const s = series.value
  if (!s || !s.points.length) return null
  const vals = [0, s.start, ...s.points.map((p) => p.cum)]
  let lo = Math.min(...vals)
  let hi = Math.max(...vals)
  if (hi - lo < 1e-9) { hi += 0.05; lo -= 0.05 }
  const pad = (hi - lo) * 0.1
  lo -= pad
  hi += pad
  const pw = W.value - M.l - M.r
  const ph = H.value - M.t - M.b
  const x = (i: number) => M.l + (i / n.value) * pw
  const y = (v: number) => M.t + ((hi - v) / (hi - lo)) * ph
  const pts: Array<[number, number]> = [[x(0), y(s.start)], ...s.points.map((p, i): [number, number] => [x(i + 1), y(p.cum)])]
  const line = smoothPath(pts)
  const y0 = y(0)
  const area = `${line}L${x(n.value).toFixed(1)} ${y0.toFixed(1)}L${x(0).toFixed(1)} ${y0.toFixed(1)}Z`
  const last = pts[pts.length - 1]
  return {
    x, y, y0, line, area, pw, ticks: niceTicks(lo, hi, 3), last, pos: s.last >= 0,
    yMax: y(Math.max(...vals)), yMin: y(Math.min(...vals)),
  }
})

const uid = 'pnl'        // виджет на странице один
const fullFmt = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false })
const tickText = (v: number) => (Math.abs(v) < 1e-9 ? '0' : Math.abs(v) >= 1000 ? `${(v / 1000).toFixed(1)}K` : Number.isInteger(v) ? String(v) : v.toFixed(Math.abs(v) < 1 ? 2 : 1).replace('-', '−'))

const active = ref<number | null>(null)      // номер сделки в периоде, 0..n-1
const pt = computed(() => (series.value && active.value != null ? series.value.points[active.value] ?? null : null))
const tip = computed(() => {
  if (!pt.value || !geo.value || active.value == null) return null
  const x = geo.value.x(active.value + 1)
  return { x, y: geo.value.y(pt.value.cum), left: Math.min(Math.max(x, 90), W.value - 90) }
})

function onMove(ev: PointerEvent): void {
  const g = geo.value
  if (!g || !n.value) return
  const px = ev.clientX - (ev.currentTarget as SVGElement).getBoundingClientRect().left
  active.value = Math.max(1, Math.min(n.value, Math.round(((px - M.l) / g.pw) * n.value))) - 1
}
function onKey(ev: KeyboardEvent): void {
  if (!n.value || (ev.key !== 'ArrowLeft' && ev.key !== 'ArrowRight')) return
  ev.preventDefault()
  active.value = Math.min(n.value - 1, Math.max(0, (active.value ?? n.value) + (ev.key === 'ArrowRight' ? 1 : -1)))
}

const tone = computed(() => ((series.value?.delta ?? 0) > 0 ? 'up' : (series.value?.delta ?? 0) < 0 ? 'down' : ''))
const summary = computed(() => (series.value ? `Накопленный результат ${signed(series.value.last)} USDT, за период ${signed(series.value.delta)}, сделок в периоде ${n.value}` : 'Закрытых сделок нет'))
const rows = computed(() => (series.value ? [...series.value.points].reverse().slice(0, 50) : []))
</script>

<template>
  <section class="chart" aria-label="Накопленный PnL">
    <header class="head">
      <h2 class="title">Накопленный PnL</h2>
      <span v-if="series" class="val" :class="tone">{{ signed(series.delta) }}<small> USDT · {{ n }} сделок</small></span>
      <UiSegment v-model="range" :options="ranges" label="Период графика" class="seg" />
    </header>

    <div ref="wrap" class="plot" :style="{ height: H + 'px' }">
      <svg v-if="geo" :width="W" :height="H" :viewBox="`0 0 ${W} ${H}`" tabindex="0" role="img" :aria-label="summary" @pointermove="onMove" @pointerleave="active = null"
           @focus="active = n - 1" @blur="active = null" @keydown="onKey">
        <defs>
          <linearGradient :id="uid + 'p'" gradientUnits="userSpaceOnUse" x1="0" x2="0" :y1="geo.y0" :y2="Math.abs(geo.yMax - geo.y0) < 1 ? geo.y0 - 1 : geo.yMax">
            <stop offset="0" stop-color="var(--pos)" stop-opacity="0" />
            <stop offset="1" stop-color="var(--pos)" stop-opacity="0.45" />
          </linearGradient>
          <linearGradient :id="uid + 'n'" gradientUnits="userSpaceOnUse" x1="0" x2="0" :y1="geo.y0" :y2="Math.abs(geo.yMin - geo.y0) < 1 ? geo.y0 + 1 : geo.yMin">
            <stop offset="0" stop-color="var(--neg)" stop-opacity="0" />
            <stop offset="1" stop-color="var(--neg)" stop-opacity="0.45" />
          </linearGradient>
          <clipPath :id="uid + 'cp'"><rect x="0" y="0" :width="W" :height="geo.y0" /></clipPath>
          <clipPath :id="uid + 'cn'"><rect x="0" :y="geo.y0" :width="W" :height="Math.max(0, H - geo.y0)" /></clipPath>
        </defs>

        <g class="grid">
          <g v-for="v in geo.ticks" :key="v">
            <line :x1="M.l" :x2="W - M.r" :y1="geo.y(v)" :y2="geo.y(v)" :class="Math.abs(v) < 1e-9 ? 'zero' : 'g'" />
            <text :x="M.l - 6" :y="geo.y(v) + 4" class="tick" text-anchor="end">{{ tickText(v) }}</text>
          </g>
        </g>

        <path :d="geo.area" :fill="`url(#${uid}p)`" :clip-path="`url(#${uid}cp)`" />
        <path :d="geo.area" :fill="`url(#${uid}n)`" :clip-path="`url(#${uid}cn)`" />
        <path :d="geo.line" class="line pos" :clip-path="`url(#${uid}cp)`" />
        <path :d="geo.line" class="line neg" :clip-path="`url(#${uid}cn)`" />

        <circle :cx="geo.last[0]" :cy="geo.last[1]" r="9" class="halo" :class="geo.pos ? 'pos' : 'neg'" />
        <circle :cx="geo.last[0]" :cy="geo.last[1]" r="4" class="end" :class="geo.pos ? 'pos' : 'neg'" />

        <g v-if="tip">
          <line class="cross" :x1="tip.x" :x2="tip.x" :y1="M.t" :y2="H - M.b" />
          <circle class="dot" :cx="tip.x" :cy="tip.y" r="4" />
        </g>
      </svg>
      <p v-else class="empty">{{ !trading.snapshot ? '' : series ? 'Сделок за этот период нет.' : 'Закрытых сделок в этом режиме ещё нет.' }}</p>

      <div v-if="tip && pt && active != null" class="tip" :style="{ left: tip.left + 'px' }">
        <b>#{{ active + 1 }}{{ pt.sym ?? '' }} {{ signed(pt.pnl, 3) }} USDT</b>
        <span>итого {{ signed(pt.cum, 3) }} · {{ fullFmt.format(new Date(pt.ts * 1000)) }}</span>
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
svg { display: block; max-width: 100%; touch-action: pan-y; outline: none; }
svg:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: var(--radius-sm); }
.grid .g { stroke: var(--line); stroke-width: 1; stroke-dasharray: 2 5; opacity: 0.9; }
.grid .zero { stroke: var(--muted); stroke-width: 1; stroke-dasharray: 4 4; opacity: 0.8; }
.tick { fill: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }
.line { fill: none; stroke-width: 2.5; stroke-linejoin: round; stroke-linecap: round; }
.line.pos, .end.pos { stroke: var(--pos); }
.line.neg, .end.neg { stroke: var(--neg); }
.halo { opacity: 0.22; }
.halo.pos { fill: var(--pos); }
.halo.neg { fill: var(--neg); }
.end { stroke: var(--panel); stroke-width: 2; }
.end.pos { fill: var(--pos); stroke: var(--panel); }
.end.neg { fill: var(--neg); stroke: var(--panel); }
.cross { stroke: var(--muted); stroke-width: 1; stroke-dasharray: 3 4; opacity: 0.6; }
.dot { fill: var(--panel); stroke: var(--accent); stroke-width: 2.5; }
.empty { margin: 0; height: 100%; display: grid; place-items: center; color: var(--muted); font-size: 13px; }
.tip { position: absolute; top: 0; transform: translateX(-50%); pointer-events: none; display: grid; gap: 2px; padding: var(--space-2) var(--space-3); background: var(--panel2); color: var(--text); border: 1px solid var(--line2); border-radius: 10px; box-shadow: 0 10px 28px rgba(0, 0, 0, 0.35); font-size: 12px; white-space: nowrap; font-variant-numeric: tabular-nums; }
.tip span { color: var(--muted); }
.tbl { margin-top: var(--space-3); font-size: 12px; color: var(--muted); }
.tbl summary { cursor: pointer; width: fit-content; }
.scroll { max-height: 200px; overflow-y: auto; margin-top: var(--space-2); }
table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
th, td { padding: 4px var(--space-2); text-align: left; border-top: 1px solid var(--line); color: var(--text); font-weight: 400; }
th { color: var(--muted); border-top: 0; position: sticky; top: 0; background: var(--panel); }
.n { text-align: right; }
</style>
