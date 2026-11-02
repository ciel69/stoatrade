<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { coinKey, HEAT_STALE_S, intensity, kfmt, METRICS, metricById, squarify, tone, useMarketStore, type CoinData, type HeatRow, type MetricId } from '@/entities/market'
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

/** Метрика цвета: выбор владельца; пока он не выбирал, берётся «15 мин», а если по ней у монет нет данных (сканер не отдаёт 15-минутные метрики), первая метрика с данными. */
const picked = ref<MetricId | null>(null)
const hasData = (id: MetricId): boolean => coins.value.some((c) => metricById(id).get(c) != null)
const metricId = computed<MetricId>({
  get: () => picked.value ?? (['ret_15m_pct', ...METRICS.map((m) => m.id)] as MetricId[]).find((id) => hasData(id)) ?? 'ret_15m_pct',
  set: (v) => { picked.value = v },
})
const metric = computed(() => metricById(metricId.value))
const noData = computed(() => !!market.snapshot && coins.value.length > 0 && !hasData(metricId.value))
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

type Scope = 'all' | 'hot' | 'feed'
const q = new URLSearchParams(location.search).get('scope')        // ?scope=hot|feed открывает карту сразу с фильтром (для проверки глазами)
const scope = ref<Scope>(q === 'hot' || q === 'feed' ? q : 'all')
const NO_TURN = 50        // оборот неизвестен (монета только в фиде): условный вес, чтобы плитка не пропала

const MAX = 60
/** Монеты карты: топ по обороту; горячие и отслеживаемые, которых нет в топе, достраиваются из своих данных (цена, ход, оборот сканера). */
const hotKeys = computed(() => new Set((market.snapshot?.hot.top ?? []).map((h) => coinKey(h.sym))))
const feedKeys = computed(() => new Set((market.snapshot?.coins.rows ?? []).map((c) => coinKey(c.sym))))
const pool = computed<HeatRow[]>(() => {
  const s = market.snapshot
  if (!s) return []
  const rows = new Map(s.heat.rows.map((r) => [coinKey(r.sym), r]))
  const add = (key: string, r: HeatRow) => { if (!rows.has(key)) rows.set(key, r) }
  const blank = { oi_chg_15m_pct: null, oi_chg_1h_pct: null, oi_musd: null, funding_pct: null, score: null, turn_surge_5m: null, range_5m_pct: null, ret_15m_pct: null, ret_5m_pct: null, chg_24h_pct: null, turn_24h_musd: null }
  for (const h of s.hot.top) add(coinKey(h.sym), { ...blank, sym: h.sym, tradable: h.tradable, price: h.price, chg_24h_pct: h.chg_24h_pct, ret_5m_pct: h.ret_5m_pct, range_5m_pct: h.range_5m_pct, turn_surge_5m: h.turn_surge_5m, turn_24h_musd: h.turn_24h_musd, score: h.score })
  for (const c of s.coins.rows) add(coinKey(c.sym), { ...blank, sym: c.sym, tradable: true, price: c.price, ret_5m_pct: c.ret_5m != null ? c.ret_5m * 100 : null, ret_15m_pct: c.ret_15m != null ? c.ret_15m * 100 : null })
  return [...rows.values()]
})
const coins = computed(() => {
  const keep = (key: string) => scope.value === 'all' || (scope.value === 'hot' ? hotKeys.value.has(key) : feedKeys.value.has(key))
  return pool.value
    .filter((r) => keep(coinKey(r.sym)) && (scope.value !== 'all' || (r.turn_24h_musd ?? 0) > 0))
    .sort((a, b) => (b.turn_24h_musd ?? NO_TURN) - (a.turn_24h_musd ?? NO_TURN))
    .slice(0, MAX)
    .map((heat): CoinData & { key: string } => ({ key: coinKey(heat.sym), heat, liq: market.liqBy.get(coinKey(heat.sym)), flow: market.flowBy.get(coinKey(heat.sym)) }))
})
const layout = computed(() => squarify(coins.value.map((c) => ({ key: c.key, weight: Math.sqrt(c.heat.turn_24h_musd ?? NO_TURN) })), { x: 0, y: 0, w: width.value, h: height.value }))
const scopes = computed(() => [
  { value: 'all' as Scope, label: 'Все' },
  { value: 'hot' as Scope, label: `Горячие${market.snapshot?.hot.available ? ' ' + hotKeys.value.size : ''}` },
  { value: 'feed' as Scope, label: `Отслеживаемые${market.snapshot?.coins.ok ? ' ' + feedKeys.value.size : ''}` },
])

/** Монеты, на которые есть живой план или идея мозга. */
const planned = computed(() => new Set((trading.plans?.items ?? []).filter((p) => p.col === 'waiting' || p.col === 'watching' || p.col === 'in_trade').map((p) => coinKey(p.sym))))
const ideated = computed(() => new Set((ideas.snapshot?.items ?? []).filter((i) => i.status === 'watching' || i.status === 'idea').map((i) => coinKey(i.sym))))

interface Tile { key: string; sym: string; x: number; y: number; w: number; h: number; value: number | null; text: string; tone: string; style: Record<string, string>; size: 'xl' | 'lg' | 'md' | 'sm' | 'xs'; price: string; plan: boolean; idea: boolean; hot: boolean; dim: boolean; label: string; i: number }
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
    size, price: price(c.heat.price), plan: planned.value.has(c.key), idea: ideated.value.has(c.key), hot: hotKeys.value.has(c.key), dim: !c.heat.tradable,
    label: `${c.key}: ${m.label} ${text}, цена ${price(c.heat.price)}, оборот ${fixed(c.heat.turn_24h_musd, 0)} млн USD${c.heat.tradable ? '' : ', сканер не допускает к торговле'}${planned.value.has(c.key) ? ', есть план' : ''}${ideated.value.has(c.key) ? ', есть идея' : ''}${hotKeys.value.has(c.key) ? ', в горячих сканера' : ''}`,
  }]
}))

const stale = computed(() => (market.snapshot?.heat.at != null ? now.value - market.snapshot.heat.at > HEAT_STALE_S : false))
const totals = computed(() => market.snapshot?.liq)
const empty = computed(() => market.snapshot && !coins.value.length)
</script>

<template>
  <section class="map" aria-label="Тепловая карта">
    <header class="top">
      <h2 class="title">Рынок</h2>
      <span v-if="market.snapshot?.heat.at" class="age" :class="{ stale }">{{ stale ? 'сканер не обновляет карту ' : 'обновлено ' }}{{ ageText(market.snapshot.heat.at, now) }}</span>
      <UiSegment v-model="scope" :options="scopes" label="Какие монеты показывать" />
      <UiSegment v-model="metricId" :options="options" label="Что показывает цвет" />
    </header>
    <p v-if="noData" class="hint warn">По метрике «{{ metric.label }}» данных пока нет: сканер её не отдаёт (после запуска ей нужна история). Выберите другую метрику.</p>
    <p v-else class="hint">{{ metric.hint }}. Размер плитки: оборот за сутки.</p>

    <p v-if="totals?.available && metricId === 'liq'" class="liq">Ликвидации за час по топу монет: лонги <b class="down">{{ kfmt(totals.totals.long) }}</b>, шорты <b class="up">{{ kfmt(totals.totals.short) }}</b> USD</p>

    <div ref="wrap" class="field" :style="{ height: height + 'px' }">
      <div v-if="market.snapshot && !empty" class="tiles">
        <button v-for="t in tiles" :key="t.key" type="button" class="tile" :class="[`s-${t.size}`, `t-${t.tone}`, { sel: selected === t.key, dim: t.dim, hot: t.hot, plan: t.plan, idea: t.idea && !t.plan, nil: t.value == null }]"
                :style="{ ...t.style, width: t.w + 'px', height: t.h + 'px', transform: `translate(${t.x}px, ${t.y}px)` }" :aria-label="t.label" :aria-pressed="selected === t.key" :title="t.label" @click="emit('select', t.key)">
          <span class="sym">{{ t.sym }}</span>
          <span v-if="t.size !== 'xs'" class="val">{{ t.text }}</span>
          <span v-if="t.size === 'xl' || t.size === 'lg'" class="px">{{ t.price }}</span>
        </button>
      </div>
      <p v-else-if="empty" class="empty">{{ scope === 'hot' ? 'Сканер пока не нашёл монет со всплеском активности.' : scope === 'feed' ? 'Фид цен не отдаёт отслеживаемых монет: нужен запущенный фид (runtime/market.json).' : 'Тепловой карты пока нет: её пишет сканер горячих монет, после запуска платформы она появляется через минуту.' }}</p>
      <div v-else class="skel" aria-busy="true" />
    </div>

    <footer class="legend">
      <span class="lg"><i class="sw plan" />есть план</span>
      <span class="lg"><i class="sw idea" />есть идея мозга</span>
      <span class="lg"><i class="sw hot" />в горячих сканера</span>
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
.hint.warn { color: var(--warn); }
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
.tile.hot::after { content: ''; position: absolute; top: 6px; right: 6px; width: 6px; height: 6px; border-radius: 50%; background: var(--warn); }
.s-xs.hot::after { top: 3px; right: 3px; width: 4px; height: 4px; }
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
.sw.hot { border-radius: 50%; width: 8px; height: 8px; background: var(--warn); }
.sw.dim { opacity: 0.45; background: var(--muted); }
.scale { margin-left: auto; display: inline-flex; align-items: center; gap: 6px; font-variant-numeric: tabular-nums; }
.bar { width: 120px; height: 8px; border-radius: 4px; background: linear-gradient(90deg, color-mix(in srgb, var(--neg) 60%, var(--panel2)), var(--panel2), color-mix(in srgb, var(--pos) 60%, var(--panel2))); }
.neg { color: var(--neg); }
.pos { color: var(--pos); }
@media (prefers-reduced-motion: reduce) {
  .tiles { animation: none; }
}
</style>
