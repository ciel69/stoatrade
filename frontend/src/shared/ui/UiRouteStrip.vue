<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch, watchPostEffect } from 'vue'
import { axisOf, clamp, guideParts, markParts, nearestMark, placeTip, tipText, type StripBand, type StripMark, type TipParts } from './route-strip'

/**
 * Общая полоса маршрута (позиции, планы, идеи, досье): ось от стопа к дальнему тейку, закрашенные отрезки и деления.
 * Наведение или фокус на деление показывает подсказку «название · цена · % от входа · деньги»; над полосой мышь рисует направляющую с ценой под курсором.
 * Деления доступны с клавиатуры (Tab), на сенсорном экране подсказка открывается касанием. Подписи под полосой (слот caption) подсвечивают деление и наоборот.
 * Движение только transform и opacity; положение делений считается из ширины полосы (--w), поэтому маркер цены плавно едет без перерасчёта раскладки.
 */
const props = defineProps<{
  marks: StripMark[]
  bands?: StripBand[]
  /** подпись над полосой (цена входа акцентом): at это доля оси */
  flag?: { at: number; text: string; markKey?: string }
  label: string
}>()

const plot = ref<HTMLElement | null>(null)
const tipEl = ref<HTMLElement | null>(null)
const w = ref(0)
const ready = ref(false)
const resizing = ref(false)
const hoverKey = ref<string | null>(null)
const focusKey = ref<string | null>(null)
const touchKey = ref<string | null>(null)
const linked = ref<string | null>(null)
const cursor = ref<number | null>(null)            // доля оси под мышью, если рядом нет деления
const anchor = ref(0)
const shown = ref<TipParts | null>(null)           // последний показанный текст: при скрытии подсказка гаснет, а не пустеет

const sorted = computed(() => props.marks.slice().sort((a, b) => a.at - b.at))
const axis = computed(() => axisOf(props.marks))
const keyed = computed(() => hoverKey.value ?? touchKey.value ?? focusKey.value)
const current = computed(() => keyed.value ?? linked.value)
const markOf = (k: string | null) => (k ? props.marks.find((m) => m.key === k) ?? null : null)
const tip = computed<TipParts | null>(() => {
  const m = markOf(keyed.value)
  if (m) return markParts(m)
  return cursor.value != null && axis.value ? guideParts(axis.value, cursor.value) : null
})
watch(tip, (v) => { if (v) shown.value = v }, { immediate: true })

watchPostEffect(() => {
  const el = tipEl.value
  const box = plot.value
  void tip.value
  void shown.value
  void anchor.value
  void w.value
  if (!el || !box) return
  el.style.setProperty('--tx', `${placeTip(anchor.value, el.offsetWidth, box.clientWidth)}px`)
})

const frac = (px: number) => clamp(px / (w.value || 1), 0, 1)

function measure(): void {
  if (plot.value) w.value = plot.value.clientWidth
}

function onMove(e: PointerEvent): void {
  if (e.pointerType === 'touch' || !plot.value) return
  measure()
  const x = e.clientX - plot.value.getBoundingClientRect().left
  const m = nearestMark(props.marks, x, w.value)
  hoverKey.value = m?.key ?? null
  cursor.value = m ? null : frac(x)
  anchor.value = m ? m.at * w.value : clamp(x, 0, w.value)
}

function onLeave(e: PointerEvent): void {
  if (e.pointerType === 'touch') return
  hoverKey.value = null
  cursor.value = null
}

function onDown(e: PointerEvent): void {
  if (e.pointerType !== 'touch' || !plot.value) return
  measure()
  const m = nearestMark(props.marks, e.clientX - plot.value.getBoundingClientRect().left, w.value, 14)
  touchKey.value = m && touchKey.value !== m.key ? m.key : null
  if (m) anchor.value = m.at * w.value
}

function onFocus(m: StripMark): void {
  measure()
  focusKey.value = m.key
  anchor.value = m.at * w.value
}

function onOutside(e: Event): void {
  if (touchKey.value && !(e.target instanceof Node && plot.value?.contains(e.target))) touchKey.value = null
}

function onEsc(): void {
  hoverKey.value = null
  cursor.value = null
  touchKey.value = null
  if (document.activeElement instanceof HTMLElement && plot.value?.contains(document.activeElement)) document.activeElement.blur()
}

const setLinked = (k: string | null): void => { linked.value = k }

let ro: ResizeObserver | null = null
let timer = 0
onMounted(() => {
  measure()
  if (typeof ResizeObserver !== 'undefined' && plot.value) {
    ro = new ResizeObserver(() => {
      measure()
      resizing.value = true
      window.clearTimeout(timer)
      timer = window.setTimeout(() => { resizing.value = false }, 150)
    })
    ro.observe(plot.value)
  }
  document.addEventListener('pointerdown', onOutside)
  void nextTick(() => requestAnimationFrame(() => requestAnimationFrame(() => { ready.value = true })))        // переходы включаются после первой раскладки, чтобы метки не ехали с нуля
})
onBeforeUnmount(() => {
  ro?.disconnect()
  window.clearTimeout(timer)
  document.removeEventListener('pointerdown', onOutside)
})

const bandStyle = (b: StripBand) => ({ '--from': String(b.from), '--span': String(Math.max(0, b.to - b.from)) })
</script>

<template>
  <div class="rs" :class="{ ready, resizing, 'has-flag': !!flag }" title="" @keydown.esc="onEsc">
    <div v-if="flag" class="flagrow">
      <span class="flag" :class="{ hot: current != null && current === flag.markKey }" :style="{ left: clamp(flag.at * 100, 10, 90) + '%' }">{{ flag.text }}</span>
    </div>
    <div ref="plot" class="plot" role="group" :aria-label="label" :style="{ '--w': w + 'px' }" @pointermove="onMove" @pointerleave="onLeave" @pointerdown="onDown">
      <i class="track" />
      <i v-for="(b, i) in bands" :key="b.kind + i" class="band" :class="`b-${b.kind}`" :style="bandStyle(b)" />
      <i class="guide" :class="{ show: cursor != null && hoverKey == null }" :style="{ '--at': String(cursor ?? 0) }" />
      <button v-for="m in sorted" :key="m.key" type="button" class="mark" :class="[`k-${m.kind}`, { on: current === m.key }]" :style="{ '--at': String(m.at) }"
              :aria-label="tipText(m)" @focus="onFocus(m)" @blur="focusKey = null" @keydown.enter.stop @keydown.space.stop />
      <span ref="tipEl" class="tip" :class="{ show: tip != null }" aria-hidden="true">
        <template v-if="shown">
          <b class="nm">{{ shown.name }}</b>
          <span class="val">{{ shown.value }}</span>
          <span v-if="shown.pct" class="x" :class="shown.pct.tone">{{ shown.pct.text }}</span>
          <span v-if="shown.money" class="x" :class="shown.money.tone">{{ shown.money.text }}</span>
        </template>
      </span>
    </div>
    <div v-if="$slots.caption" class="cap"><slot name="caption" :active="current" :set="setLinked" /></div>
  </div>
</template>

<style scoped>
.rs { --h: 20px; position: relative; min-width: 0; }
.flagrow { position: relative; height: 14px; margin-bottom: 0; }
.flag { position: absolute; top: 0; transform: translateX(-50%); font-size: 11px; font-weight: 600; line-height: 14px; color: var(--accent); font-variant-numeric: tabular-nums; white-space: nowrap; }
.plot { position: relative; height: var(--h); }
.plot::before { content: ""; position: absolute; inset: -7px -8px; }        /* зона наведения шире полосы */
.track { position: absolute; left: 0; right: 0; top: 9px; height: 2px; background: var(--line2); }
.band { position: absolute; left: 0; top: 7.5px; width: 100%; height: 5px; transform-origin: 0 50%; transform: translateX(calc(var(--w, 0px) * var(--from))) scaleX(var(--span)); }
.b-risk { background: var(--neg); opacity: 0.22; }
.b-locked { background: var(--pos); opacity: 0.28; }
.b-gain { background: var(--pos); opacity: 0.55; }
.b-loss { background: var(--neg); opacity: 0.55; }
.b-gap { background: var(--accent); opacity: 0.5; }

.mark { position: absolute; left: 0; top: 0; width: 16px; height: 100%; margin: 0 0 0 -8px; padding: 0; border: 0; border-radius: 3px; background: none; color: inherit; pointer-events: none; transform: translateX(calc(var(--w, 0px) * var(--at))); }
.mark::before { content: ""; position: absolute; left: 50%; top: var(--in, 0); bottom: var(--in, 0); width: var(--lw, 2px); margin-left: calc(var(--lw, 2px) / -2); border-radius: 1px; background: var(--c); transition: transform var(--dur-fast) var(--ease-out), opacity var(--dur-fast) var(--ease-out); }
.mark:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.mark.on::before { transform: scale(1.7, 1.28); opacity: 1; }
.k-stop { --c: var(--neg); }
.k-safe { --c: var(--pos); }
.k-entry { --c: var(--muted); --in: 1px; }
.k-level { --c: var(--accent); --in: 1px; }
.k-take { --c: var(--pos); --in: 4px; }
.k-take::before { opacity: 0.8; }
.k-now { --c: var(--text); --lw: 3px; z-index: 1; }

.guide { position: absolute; left: 0; top: -3px; bottom: -3px; width: 1px; background: var(--muted); opacity: 0; transform: translateX(calc(var(--w, 0px) * var(--at))); pointer-events: none; transition: opacity var(--dur-fast) var(--ease-out); }
.guide.show { opacity: 0.55; }

.tip { position: absolute; left: 0; bottom: calc(100% + 8px); z-index: 5; display: inline-flex; align-items: baseline; gap: var(--space-2); padding: 3px var(--space-2); border: 1px solid var(--line2); border-radius: var(--radius-sm); background: var(--panel2); box-shadow: var(--lift); font-size: 11px; line-height: 1.4; color: var(--text); white-space: nowrap; font-variant-numeric: tabular-nums; pointer-events: none; opacity: 0; translate: 0 3px; transform: translateX(var(--tx, 0px)); }
.has-flag .tip { bottom: calc(100% + 22px); }        /* выше подписи уровня, чтобы не закрывать её */
.tip.show { opacity: 1; translate: 0 0; transition: opacity var(--dur-fast) var(--ease-out), translate var(--dur-fast) var(--ease-out); }
.tip:not(.show) { transition: opacity 100ms ease; }
.nm { font-weight: 600; color: var(--muted); }
.x.up { color: var(--pos); }
.x.down { color: var(--neg); }

.ready:not(.resizing) .band, .ready:not(.resizing) .mark.k-now { transition: transform 240ms var(--ease-out); }

.cap { display: flex; flex-wrap: wrap; align-items: baseline; column-gap: var(--space-3); row-gap: 2px; margin-top: var(--space-1); font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.cap :slotted(.rs-i) { display: inline-flex; flex-wrap: wrap; align-items: baseline; column-gap: var(--space-2); row-gap: 2px; min-width: 0; padding: 1px 6px; margin: 0 -6px; border-radius: var(--radius-sm); transition: background-color var(--dur-fast) ease; }
.cap :slotted(.rs-i.hot) { background: var(--panel2); }
.cap :slotted(.rs-end) { margin-left: auto; justify-content: flex-end; text-align: right; }
.cap :slotted(.rs-mid) { margin-inline: auto; }                       /* свободное место делится поровну: стоп слева, срок между, тейк справа; при переносе тейк остаётся у правого края */
.cap :slotted(.rs-i b) { font-weight: 600; }
.cap :slotted(.rs-i .up) { color: var(--pos); }
.cap :slotted(.rs-i .down) { color: var(--neg); }

.flag { transition: opacity var(--dur-fast) ease; }
.flag.hot { color: var(--text); }

@media (prefers-reduced-motion: reduce) {
  .ready:not(.resizing) .band, .ready:not(.resizing) .mark.k-now { transition: none; }
  .mark.on::before { transform: none; }
  .mark.on::before { filter: brightness(1.35); }
  .tip, .tip.show { translate: 0 0; }
}
</style>
