<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { PAD, fmtNum, layoutOf, nearestIndex, pointText, stepIndex, summaryText, type BandPoint } from './band-chart'

/**
 * Общий график «скользящее значение и полоса интервала»: ряд, лента интервала, нулевая линия, точка последнего значения.
 * SVG без зависимостей; ширина берётся у контейнера, размеры текста остаются реальными (без масштабирования viewBox).
 * Подсказка по наведению и по клавиатуре (Tab на график, стрелки, Home/End, Esc); текстовая альтернатива: aria-label и таблица для читалок.
 * Цвета только из tokens.css; ленты нет, где интервал неизвестен (null). Данные не двигаются: анимируется только появление (opacity, translate).
 */
const props = withDefaults(defineProps<{
  points: BandPoint[]
  /** название ряда: «Средний R, окно 50» */
  label: string
  /** единица после значения в подсказке и таблице: «R» */
  unit?: string
  /** подпись столбца и оси X в таблице: «Сделка» */
  xLabel?: string
  height?: number
  /** фиксированная ширина в пикселях; по умолчанию ширина контейнера */
  width?: number
  format?: (v: number) => string
  emptyText?: string
}>(), { unit: '', xLabel: 'Точка', height: 120, emptyText: 'Данных пока нет' })

const box = ref<HTMLElement | null>(null)
const measured = ref(0)
const ready = ref(false)
const active = ref<number | null>(null)

const fmt = computed(() => props.format ?? ((v: number) => fmtNum(v)))
const w = computed(() => props.width ?? (measured.value || 320))
const lay = computed(() => layoutOf(props.points, w.value, props.height, undefined, fmt.value))
const count = computed(() => lay.value.pts.length)
const sel = computed(() => (active.value != null && active.value < count.value ? active.value : null))
const aria = computed(() => summaryText(props.label, lay.value.pts, fmt.value, props.unit))
const lastIx = computed(() => count.value - 1)
const lastLabel = computed(() => (count.value ? fmt.value(lay.value.pts[lastIx.value]!.v) : ''))
const lastLabelY = computed(() => Math.max(PAD.t + 8, (lay.value.ys[lastIx.value] ?? 0) - 8))
const tipText = computed(() => (sel.value != null ? pointText(lay.value.pts[sel.value]!, fmt.value, props.unit) : ''))
const tipSide = computed(() => {
  const x = sel.value != null ? lay.value.xs[sel.value]! : 0
  return x < w.value * 0.28 ? 'l' : x > w.value * 0.72 ? 'r' : 'c'
})
const tipTop = computed(() => (sel.value != null ? lay.value.ys[sel.value]! : 0))
const tipBelow = computed(() => tipTop.value < 40)

function measure(): void {
  if (box.value) measured.value = box.value.clientWidth
}

function onMove(e: PointerEvent): void {
  if (!box.value || !count.value || e.pointerType === 'touch') return
  active.value = nearestIndex(lay.value.xs, e.clientX - box.value.getBoundingClientRect().left)
}

function onDown(e: PointerEvent): void {
  if (!box.value || !count.value || e.pointerType !== 'touch') return
  const i = nearestIndex(lay.value.xs, e.clientX - box.value.getBoundingClientRect().left)
  active.value = active.value === i ? null : i
}

function onKey(e: KeyboardEvent): void {
  if (e.key === 'Escape') { active.value = null; return }
  const next = stepIndex(sel.value, count.value, e.key)
  if (next == null) return
  e.preventDefault()
  active.value = next
}

let ro: ResizeObserver | null = null
onMounted(() => {
  measure()
  if (typeof ResizeObserver !== 'undefined' && box.value && props.width == null) {
    ro = new ResizeObserver(measure)
    ro.observe(box.value)
  }
  void nextTick(() => requestAnimationFrame(() => { ready.value = true }))        // переход включается после первой раскладки, иначе появление не видно
})
onBeforeUnmount(() => ro?.disconnect())
</script>

<template>
  <div ref="box" class="bc" :class="{ ready }">
    <div v-if="!count" class="empty" :style="{ height: height + 'px' }" role="img" :aria-label="aria">{{ emptyText }}</div>
    <div v-else class="chart" tabindex="0" role="group" :aria-label="aria" @pointermove="onMove" @pointerdown="onDown" @pointerleave="active = null" @keydown="onKey" @blur="active = null">
      <svg class="svg" :width="w" :height="height" :viewBox="`0 0 ${w} ${height}`" aria-hidden="true" focusable="false">
        <g class="ticks">
          <g v-for="t in lay.ticks" :key="t.text + t.y">
            <line v-if="!t.zero" class="grid" :x1="PAD.l" :x2="w - PAD.r" :y1="t.y" :y2="t.y" />
            <text class="tl" :class="{ z: t.zero }" :x="PAD.l - 4" :y="t.y" text-anchor="end" dominant-baseline="central">{{ t.text }}</text>
          </g>
        </g>
        <g class="series">
          <path v-for="(d, k) in lay.bands" :key="k" class="band" :d="d" />
          <line v-for="(s, k) in lay.whiskers" :key="'w' + k" class="whisker" :x1="s.x" :x2="s.x" :y1="s.y1" :y2="s.y2" />
          <path v-if="lay.line" class="line" :d="lay.line" />
          <line class="zero" :x1="PAD.l" :x2="w - PAD.r" :y1="lay.zeroY" :y2="lay.zeroY" />
          <circle class="last-ring" :cx="lay.xs[lastIx]" :cy="lay.ys[lastIx]" r="5" />
          <circle class="last" :cx="lay.xs[lastIx]" :cy="lay.ys[lastIx]" r="3" />
          <text class="lv" :x="Math.min(w - PAD.r, lay.xs[lastIx]! + 4)" :y="lastLabelY" text-anchor="end">{{ lastLabel }}</text>
        </g>
        <g class="cursor" :class="{ show: sel != null }" aria-hidden="true">
          <template v-if="sel != null">
            <line class="cur-line" :x1="lay.xs[sel]" :x2="lay.xs[sel]" :y1="PAD.t" :y2="height - PAD.b" />
            <circle class="cur-ring" :cx="lay.xs[sel]" :cy="lay.ys[sel]" r="5" />
            <circle class="cur-dot" :cx="lay.xs[sel]" :cy="lay.ys[sel]" r="3" />
          </template>
        </g>
      </svg>
      <span v-if="sel != null" class="tip" :class="[`s-${tipSide}`, { below: tipBelow }]" :style="{ left: lay.xs[sel] + 'px', top: tipTop + 'px' }" aria-hidden="true">{{ tipText }}</span>
      <table class="sr">
        <caption>{{ label }}</caption>
        <thead>
          <tr><th>{{ xLabel }}</th><th>Значение{{ unit ? `, ${unit}` : '' }}</th><th>Интервал снизу</th><th>Интервал сверху</th><th>n</th></tr>
        </thead>
        <tbody>
          <tr v-for="(p, k) in lay.pts" :key="k">
            <td>{{ p.i }}</td><td>{{ fmt(p.v) }}</td><td>{{ p.lo != null ? fmt(p.lo) : 'нет' }}</td><td>{{ p.hi != null ? fmt(p.hi) : 'нет' }}</td><td>{{ p.n ?? '' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.bc { position: relative; min-width: 0; width: 100%; }
.chart { position: relative; display: block; width: 100%; border-radius: var(--radius-sm); touch-action: pan-y; -webkit-tap-highlight-color: transparent; }
.chart:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.svg { display: block; max-width: 100%; overflow: visible; }
.empty { display: flex; align-items: center; justify-content: center; border: 1px dashed var(--line2); border-radius: var(--radius-sm); color: var(--muted); font-size: 12px; }

.grid { stroke: var(--line); stroke-width: 1; }
.tl { fill: var(--muted); font: 11px var(--font); font-variant-numeric: tabular-nums; }
.tl.z { fill: var(--text); }
.zero { stroke: var(--line2); stroke-width: 1; stroke-dasharray: 3 3; }
.band { fill: var(--accent); fill-opacity: 0.2; stroke: none; }
.whisker { stroke: var(--accent); stroke-opacity: 0.5; stroke-width: 2; stroke-linecap: round; }
.line { fill: none; stroke: var(--accent); stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }
.last { fill: var(--accent); }
.last-ring, .cur-ring { fill: var(--panel); }
.cur-dot { fill: var(--text); }
.cur-line { stroke: var(--muted); stroke-width: 1; stroke-opacity: 0.6; }
.lv { fill: var(--text); font: 600 11px var(--font); font-variant-numeric: tabular-nums; paint-order: stroke; stroke: var(--panel); stroke-width: 3px; stroke-linejoin: round; }

.tip { position: absolute; z-index: 5; max-width: calc(100% - 8px); padding: 3px var(--space-2); border: 1px solid var(--line2); border-radius: var(--radius-sm); background: var(--panel2); box-shadow: var(--lift); font-size: 11px; line-height: 1.4; color: var(--text); white-space: nowrap; font-variant-numeric: tabular-nums; pointer-events: none; transform: translate(-50%, calc(-100% - 10px)); }
.tip.s-l { transform: translate(-8px, calc(-100% - 10px)); }
.tip.s-r { transform: translate(calc(-100% + 8px), calc(-100% - 10px)); }
.tip.below { transform: translate(-50%, 12px); }
.tip.below.s-l { transform: translate(-8px, 12px); }
.tip.below.s-r { transform: translate(calc(-100% + 8px), 12px); }

/* для читалок: таблица есть, но не занимает места */
.sr { position: absolute; width: 1px; height: 1px; margin: -1px; padding: 0; overflow: hidden; clip-path: inset(50%); white-space: nowrap; border: 0; }

/* появление: только opacity и сдвиг на 4 px, один раз после первой раскладки; данные при обновлении не едут */
.series, .ticks { opacity: 0; transform: translateY(4px); }
.ready .series, .ready .ticks { opacity: 1; transform: none; transition: opacity var(--dur-enter) var(--ease-out), transform var(--dur-enter) var(--ease-out); }
.cursor { opacity: 0; transition: opacity var(--dur-fast) var(--ease-out); }
.cursor.show { opacity: 1; }

@media (prefers-reduced-motion: reduce) {
  .series, .ticks { transform: none; }
  .ready .series, .ready .ticks { transition: opacity var(--dur-enter) ease; }
}
</style>
