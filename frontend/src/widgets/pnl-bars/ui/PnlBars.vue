<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { niceTicks, useTradingStore } from '@/entities/trading'
import { signed } from '@/shared/lib'

/** Результат последних сделок столбиками: вверх плюс, вниз минус, один нулевой уровень. Столбик не толще 22 px, скруглён, подпись по наведению и фокусу. */
const MAX = 40
const trading = useTradingStore()
const M = { l: 42, r: 10, t: 10, b: 8 }
const wrap = ref<HTMLElement | null>(null)
const W = ref(400)
const H = computed(() => (W.value < 420 ? 150 : 170))
let ro: ResizeObserver | null = null
onMounted(() => {
  if (!wrap.value) return
  W.value = Math.max(240, Math.round(wrap.value.clientWidth))
  ro = new ResizeObserver(() => wrap.value && (W.value = Math.max(240, Math.round(wrap.value.clientWidth))))
  ro.observe(wrap.value)
})
onUnmounted(() => ro?.disconnect())

const all = computed(() => trading.snapshot?.curve?.points ?? [])
const pts = computed(() => all.value.slice(-MAX))
const dateFmt = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false })

const geo = computed(() => {
  if (!pts.value.length) return null
  const vals = pts.value.map((p) => p[2])
  let lo = Math.min(0, ...vals)
  let hi = Math.max(0, ...vals)
  if (hi - lo < 1e-9) hi = lo + 0.1
  const pad = (hi - lo) * 0.08
  if (lo < 0) lo -= pad
  hi += pad
  const slot = (W.value - M.l - M.r) / pts.value.length
  const bw = Math.max(2, Math.min(22, slot - 2))
  const y = (v: number) => M.t + ((hi - v) / (hi - lo)) * (H.value - M.t - M.b)
  const bars = pts.value.map((p, i) => {
    const x = M.l + slot * i + (slot - bw) / 2
    const y0 = y(0)
    const y1 = y(p[2])
    return { x, y: Math.min(y0, y1), h: Math.max(1, Math.abs(y1 - y0)), w: bw, hit: M.l + slot * i, slot, pos: p[2] > 0, neg: p[2] < 0, p }
  })
  return { y, bars, ticks: niceTicks(lo, hi, 3) }
})

const active = ref<number | null>(null)
const tip = computed(() => {
  const b = active.value != null ? geo.value?.bars[active.value] : null
  return b ? { left: Math.min(Math.max(b.hit + b.slot / 2, 90), W.value - 90), text: `${b.p[3] ?? ''} ${signed(b.p[2], 3)} USDT`, date: dateFmt.format(new Date(b.p[0] * 1000)) } : null
})
const tickText = (v: number) => (Math.abs(v) < 1e-9 ? '0' : Number.isInteger(v) ? String(v) : v.toFixed(Math.abs(v) < 1 ? 2 : 1).replace('-', '−'))
</script>

<template>
  <section class="bars" aria-label="PnL по сделкам">
    <h2 class="title">PnL по сделкам<span v-if="all.length" class="n">{{ all.length > MAX ? `последние ${MAX} из ${all.length}` : all.length }}</span></h2>
    <div ref="wrap" class="plot" :style="{ height: H + 'px' }">
      <svg v-if="geo" :width="W" :height="H" :viewBox="`0 0 ${W} ${H}`" role="img" :aria-label="`Результат последних ${pts.length} сделок`" @pointerleave="active = null">
        <g v-for="v in geo.ticks" :key="v">
          <line :x1="M.l" :x2="W - M.r" :y1="geo.y(v)" :y2="geo.y(v)" :class="Math.abs(v) < 1e-9 ? 'zero' : 'g'" />
          <text :x="M.l - 6" :y="geo.y(v) + 4" class="tick" text-anchor="end">{{ tickText(v) }}</text>
        </g>
        <rect v-for="(b, i) in geo.bars" :key="'b' + i" :x="b.x" :y="b.y" :width="b.w" :height="b.h" :rx="Math.min(4, b.w / 2)" class="bar" :class="[b.pos ? 'pos' : b.neg ? 'neg' : 'flat', { on: active === i }]" />
        <rect v-for="(b, i) in geo.bars" :key="'h' + i" :x="b.hit" y="0" :width="b.slot" :height="H" fill="transparent" tabindex="0" :aria-label="`${b.p[3] ?? 'сделка'} ${signed(b.p[2], 3)} USDT`"
              @pointermove="active = i" @focus="active = i" @blur="active = null" />
      </svg>
      <p v-else class="empty">{{ trading.snapshot ? 'Нет сделок для столбиков.' : '' }}</p>
      <div v-if="tip" class="tip" :style="{ left: tip.left + 'px' }"><b>{{ tip.text }}</b><span>{{ tip.date }}</span></div>
    </div>
  </section>
</template>

<style scoped>
.bars { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: var(--space-4); min-width: 0; }
.title { margin: 0 0 var(--space-3); font-size: 14px; font-weight: 600; display: flex; align-items: baseline; gap: var(--space-2); }
.n { font-size: 12px; font-weight: 400; color: var(--muted); }
.plot { position: relative; }
svg { display: block; max-width: 100%; }
.g { stroke: var(--line); stroke-width: 1; stroke-dasharray: 2 5; }
.zero { stroke: var(--line2); stroke-width: 1; }
.tick { fill: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }
.bar { opacity: 0.88; transition: opacity var(--dur-fast) ease; }
.bar.pos { fill: var(--pos); }
.bar.neg { fill: var(--neg); }
.bar.flat { fill: var(--muted); }
.bar.on { opacity: 1; }
rect:focus-visible { outline: none; }
.empty { margin: 0; height: 100%; display: grid; place-items: center; color: var(--muted); font-size: 13px; }
.tip { position: absolute; top: 0; transform: translateX(-50%); pointer-events: none; display: grid; gap: 2px; padding: var(--space-2) var(--space-3); background: var(--panel2); border: 1px solid var(--line2); border-radius: 10px; box-shadow: 0 10px 28px rgba(0, 0, 0, 0.35); font-size: 12px; white-space: nowrap; font-variant-numeric: tabular-nums; }
.tip span { color: var(--muted); }
</style>
