<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { coinKey, intensity, kfmt, METRICS, metricById, squarify, tone, useMarketStore, type CoinData, type MetricId } from '@/entities/market'
import { useIdeaStore } from '@/entities/idea'
import { useTradingStore } from '@/entities/trading'
import { UiSegment } from '@/shared/ui'
import { ageText, fixed, price, useNow } from '@/shared/lib'

/**
 * Тепловая карта рынка: плитка на монету, площадь плитки по обороту за сутки (корень, чтобы BTC не съел остальных), цвет по выбранной метрике (цена, открытый интерес, фандинг,
 * ликвидации, ордерфлоу). Число всегда рядом с цветом. Ободок: на монете есть план (акцент) или идея мозга (пунктир). Клик или Enter выбирает монету, панель держит страница.
 */
const props = defineProps<{ selected: string | null }>()
const emit = defineEmits<{ select: [sym: string] }>()
const market = useMarketStore()
const trading = useTradingStore()
const ideas = useIdeaStore()
const now = useNow()

const metricId = ref<MetricId>('ret_15m_pct')
const metric = computed(() => metricById(metricId.value))
const options = METRICS.map((m) => ({ value: m.id, label: m.label }))

const wrap = ref<HTMLElement | null>(null)
const width = ref(0)
let ro: ResizeObserver | null = null
onMounted(() => {
  if (!wrap.value) return
  width.value = wrap.value.clientWidth
  ro = new ResizeObserver(([e]) => { width.value = Math.round(e.contentRect.width) })
  ro.observe(wrap.value)
})
onUnmounted(() => ro?.disconnect())
const height = computed(() => Math.round(Math.min(600, Math.max(340, width.value * 0.5))))

const MAX = 60
const coins = computed(() => {
  const s = market.snapshot
  if (!s) return []
  return s.heat.rows
    .filter((r) => (r.turn_24h_musd ?? 0) > 0)
    .sort((a, b) => (b.turn_24h_musd ?? 0) - (a.turn_24h_musd ?? 0))
    .slice(0, MAX)
    .map((heat): CoinData & { key: string } => ({ key: coinKey(heat.sym), heat, liq: market.liqBy.get(coinKey(heat.sym)), flow: market.flowBy.get(coinKey(heat.sym)) }))
})
const layout = computed(() => squarify(coins.value.map((c) => ({ key: c.key, weight: Math.sqrt(c.heat.turn_24h_musd ?? 0) })), { x: 0, y: 0, w: width.value, h: height.value }))

/** Монеты, на которые есть живой план или идея мозга. */
const planned = computed(() => new Set((trading.plans?.items ?? []).filter((p) => p.col === 'waiting' || p.col === 'watching' || p.col === 'in_trade').map((p) => coinKey(p.sym))))
const ideated = computed(() => new Set((ideas.snapshot?.items ?? []).filter((i) => i.status === 'watching' || i.status === 'idea').map((i) => coinKey(i.sym))))

interface Tile { key: string; sym: string; x: number; y: number; w: number; h: number; value: number | null; text: string; tone: string; style: Record<string, string>; size: 'xl' | 'lg' | 'md' | 'sm' | 'xs'; price: string; plan: boolean; idea: boolean; dim: boolean; label: string; i: number }
const tiles = computed<Tile[]>(() => coins.value.flatMap((c, i) => {
  const r = layout.value.get(c.key)
  if (!r) return []
  const m = metric.value
  const v = m.get(c)
  const k = intensity(v, m)
  const t = tone(v, m)
  const base = t === 'warn' ? 'var(--warn)' : t === 'down' ? 'var(--neg)' : 'var(--pos)'
  const area = r.w * r.h
  const size = area > 26000 ? 'xl' : area > 11000 ? 'lg' : area > 4200 ? 'md' : area > 1500 ? 'sm' : 'xs'
  const text = v == null ? '–' : m.text(v)
  return [{
    key: c.key, sym: c.key, ...r, value: v, text, tone: t, i,
    style: { background: v == null ? 'var(--panel2)' : `color-mix(in srgb, ${base} ${Math.round(8 + 52 * k)}%, var(--panel2))` },
    size, price: price(c.heat.price), plan: planned.value.has(c.key), idea: ideated.value.has(c.key), dim: !c.heat.tradable,
    label: `${c.key}: ${m.label} ${text}, цена ${price(c.heat.price)}, оборот ${fixed(c.heat.turn_24h_musd, 0)} млн USD${c.heat.tradable ? '' : ', сканер не допускает к торговле'}${planned.value.has(c.key) ? ', есть план' : ''}${ideated.value.has(c.key) ? ', есть идея' : ''}`,
  }]
}))

const stale = computed(() => (market.snapshot?.heat.at != null ? now.value - market.snapshot.heat.at > 120 : false))
const totals = computed(() => market.snapshot?.liq)
const empty = computed(() => market.snapshot && (!market.snapshot.heat.available || !coins.value.length))
</script>

<template>
  <section class="map" aria-label="Тепловая карта">
    <header class="top">
      <h2 class="title">Рынок</h2>
      <span v-if="market.snapshot?.heat.at" class="age" :class="{ stale }">{{ stale ? 'сканер не обновляет карту ' : 'обновлено ' }}{{ ageText(market.snapshot.heat.at, now) }}</span>
      <UiSegment v-model="metricId" :options="options" label="Что показывает цвет" />
    </header>
    <p class="hint">{{ metric.hint }}. Размер плитки: оборот за сутки.</p>

    <p v-if="totals?.available && metricId === 'liq'" class="liq">Ликвидации за час по топу монет: лонги <b class="down">{{ kfmt(totals.totals.long) }}</b>, шорты <b class="up">{{ kfmt(totals.totals.short) }}</b> USD</p>

    <div ref="wrap" class="field" :style="{ height: height + 'px' }">
      <div v-if="market.snapshot && !empty" class="tiles">
        <button v-for="t in tiles" :key="t.key" type="button" class="tile" :class="[`s-${t.size}`, `t-${t.tone}`, { sel: selected === t.key, dim: t.dim, plan: t.plan, idea: t.idea && !t.plan, nil: t.value == null }]"
                :style="{ ...t.style, width: t.w + 'px', height: t.h + 'px', transform: `translate(${t.x}px, ${t.y}px)` }" :aria-label="t.label" :aria-pressed="selected === t.key" :title="t.label" @click="emit('select', t.key)">
          <span class="sym">{{ t.sym }}</span>
          <span v-if="t.size !== 'xs'" class="val">{{ t.text }}</span>
          <span v-if="t.size === 'xl' || t.size === 'lg'" class="px">{{ t.price }}</span>
        </button>
      </div>
      <p v-else-if="empty" class="empty">Тепловой карты пока нет: её пишет сканер горячих монет, после запуска платформы она появляется через минуту.</p>
      <div v-else class="skel" aria-busy="true" />
    </div>

    <footer class="legend">
      <span class="lg"><i class="sw plan" />есть план</span>
      <span class="lg"><i class="sw idea" />есть идея мозга</span>
      <span class="lg"><i class="sw dim" />сканер не допускает</span>
      <span v-if="metric.kind === 'signed'" class="scale" aria-hidden="true"><span class="neg">−{{ metric.id === 'liq' ? kfmt(metric.scale) : metric.scale }}</span><i class="bar" /><span class="pos">+{{ metric.id === 'liq' ? kfmt(metric.scale) : metric.scale }}</span></span>
    </footer>
  </section>
</template>

<style scoped>
.map { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: var(--space-4); min-width: 0; display: grid; gap: var(--space-3); }
.top { display: flex; align-items: center; flex-wrap: wrap; gap: var(--space-3); }
.title { margin: 0; font-size: 14px; font-weight: 600; }
.age { margin-right: auto; font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.age.stale { color: var(--warn); }
.hint, .liq { margin: 0; font-size: 12px; color: var(--muted); }
.up { color: var(--pos); }
.down { color: var(--neg); }
.field { position: relative; width: 100%; overflow: hidden; border-radius: var(--radius-sm); background: var(--bg); }
.tile {
  position: absolute; left: 0; top: 0; box-sizing: border-box; padding: 6px 8px; display: flex; flex-direction: column; align-items: flex-start; justify-content: flex-start; gap: 1px;
  border: 1px solid color-mix(in srgb, var(--bg) 70%, transparent); border-radius: 3px; color: var(--text); font: inherit; text-align: left; cursor: pointer; overflow: hidden;
  transition: background-color 400ms ease, filter var(--dur-fast) ease;
}
.tile .sym { font-weight: 700; letter-spacing: -0.01em; line-height: 1.1; }
.tile .val { font-variant-numeric: tabular-nums; font-weight: 600; opacity: 0.92; line-height: 1.1; }
.tile .px { margin-top: auto; font-size: 11px; color: var(--muted); font-variant-numeric: tabular-nums; }
.s-xl .sym { font-size: 22px; } .s-xl .val { font-size: 18px; }
.s-lg .sym { font-size: 17px; } .s-lg .val { font-size: 14px; }
.s-md .sym { font-size: 14px; } .s-md .val { font-size: 12px; }
.s-sm { padding: 4px 6px; } .s-sm .sym { font-size: 12px; } .s-sm .val { font-size: 11px; }
.s-xs { padding: 2px 4px; } .s-xs .sym { font-size: 10px; }
.tile.nil { color: var(--muted); }
.tile.dim .sym { opacity: 0.6; }
.tile.plan { box-shadow: inset 0 0 0 2px var(--accent); }
.tile.idea { box-shadow: inset 0 0 0 1.5px color-mix(in srgb, var(--accent) 70%, transparent); border-style: dashed; }
.tile.sel { outline: 2px solid var(--text); outline-offset: -2px; z-index: 1; }
.tile:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; z-index: 1; }
@media (hover: hover) and (pointer: fine) {
  .tile:not(.sel):hover { filter: brightness(1.18); z-index: 1; }
}
.tiles { position: absolute; inset: 0; animation: tiles-in 260ms var(--ease-out); }
@keyframes tiles-in { from { opacity: 0; } }
.empty { margin: 0; padding: var(--space-5); font-size: 13px; color: var(--muted); max-width: 60ch; }
.skel { position: absolute; inset: 0; background: var(--panel2); }
.legend { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-2) var(--space-4); font-size: 11px; color: var(--muted); }
.lg { display: inline-flex; align-items: center; gap: 6px; }
.sw { width: 12px; height: 12px; border-radius: 3px; background: var(--panel2); box-sizing: border-box; }
.sw.plan { box-shadow: inset 0 0 0 2px var(--accent); }
.sw.idea { border: 1.5px dashed color-mix(in srgb, var(--accent) 70%, transparent); }
.sw.dim { opacity: 0.45; background: var(--muted); }
.scale { margin-left: auto; display: inline-flex; align-items: center; gap: 6px; font-variant-numeric: tabular-nums; }
.bar { width: 120px; height: 8px; border-radius: 4px; background: linear-gradient(90deg, color-mix(in srgb, var(--neg) 60%, var(--panel2)), var(--panel2), color-mix(in srgb, var(--pos) 60%, var(--panel2))); }
.neg { color: var(--neg); }
.pos { color: var(--pos); }
@media (prefers-reduced-motion: reduce) {
  .tiles { animation: none; }
}
</style>
