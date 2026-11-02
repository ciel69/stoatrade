<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, useId, watch } from 'vue'
import { placeFloating, type Placement, type TipContent } from './tip'

defineOptions({ inheritAttrs: false })        // два корня (кнопка и Teleport): class и style родителя должны попасть на кнопку

/**
 * Стилизованная подсказка вместо нативного title. Слот это то, на что наводят (чип, число, слово); `tip` это содержимое.
 * Мышь: наведение. Клавиатура: фокус через Tab, Enter/Space переключают. Сенсорный экран: касание открывает, второе касание или касание в другом месте закрывает.
 * Esc закрывает (и не доходит до окон под ней). Окно подсказки уходит в body, поэтому не обрезается карточкой; закрывается при прокрутке и смене размера.
 * Появление: прозрачность и сдвиг 4 px за 150 мс; при prefers-reduced-motion только прозрачность. Роль tooltip, на элементе aria-describedby, пока подсказка открыта.
 */
const props = defineProps<{ tip: TipContent; dotted?: boolean; /** имя кнопки, если внутри нет текста (точки, значок) */ label?: string }>()

const id = useId()
const open = ref(false)
const trigger = ref<HTMLElement | null>(null)
const pop = ref<HTMLElement | null>(null)
const place = ref<Placement>({ x: 0, y: 0, side: 'below' })
let lastType = ''
let hoverTimer = 0
let openTop = 0
let openLeft = 0

function reposition(): void {
  const t = trigger.value
  const p = pop.value
  if (!t || !p) return
  const r = t.getBoundingClientRect()
  openTop = r.top
  openLeft = r.left
  place.value = placeFloating({ left: r.left, top: r.top, width: r.width, height: r.height }, { width: p.offsetWidth, height: p.offsetHeight }, document.documentElement.clientWidth, window.innerHeight)
}

function show(): void {
  window.clearTimeout(hoverTimer)
  open.value = true
}
function hide(): void {
  window.clearTimeout(hoverTimer)
  open.value = false
}

function onEnter(e: PointerEvent): void {
  if (e.pointerType === 'touch') return
  window.clearTimeout(hoverTimer)
  hoverTimer = window.setTimeout(show, 90)        // короткая задержка: мышь, пролетающая над строкой, не мигает подсказками
}
function onLeave(e: PointerEvent): void {
  if (e.pointerType === 'touch') return
  hide()
}
function onDown(e: PointerEvent): void {
  lastType = e.pointerType
}
function onClick(e: MouseEvent): void {
  e.stopPropagation()        // касание подсказки не должно выбирать строку списка под ней
  if (lastType === 'mouse' || lastType === 'pen') { show(); return }        // у мыши подсказку уже открыло наведение
  open.value ? hide() : show()
}
function onFocus(e: FocusEvent): void {
  let visible = false
  try { visible = (e.target as HTMLElement).matches(':focus-visible') } catch { visible = true }
  if (visible) show()
}

/** Закрываем, только если кнопка действительно уехала (запоздалое событие прокрутки после открытия подсказку не гасит). */
function onScroll(): void {
  const r = trigger.value?.getBoundingClientRect()
  if (!r || Math.abs(r.top - openTop) > 2 || Math.abs(r.left - openLeft) > 2) hide()
}

function onDocDown(e: Event): void {
  if (e.target instanceof Node && trigger.value?.contains(e.target)) return
  hide()
}
function onKey(e: KeyboardEvent): void {
  if (e.key !== 'Escape') return
  e.stopPropagation()
  hide()
}

watch(open, async (v) => {
  if (v) {
    document.addEventListener('pointerdown', onDocDown, true)
    document.addEventListener('keydown', onKey, true)
    window.addEventListener('scroll', onScroll, true)
    window.addEventListener('resize', hide)
    await nextTick()
    reposition()
  } else {
    document.removeEventListener('pointerdown', onDocDown, true)
    document.removeEventListener('keydown', onKey, true)
    window.removeEventListener('scroll', onScroll, true)
    window.removeEventListener('resize', hide)
  }
})
watch(() => props.tip, () => { if (open.value) void nextTick(reposition) }, { deep: true })

onBeforeUnmount(() => {
  window.clearTimeout(hoverTimer)
  document.removeEventListener('pointerdown', onDocDown, true)
  document.removeEventListener('keydown', onKey, true)
  window.removeEventListener('scroll', onScroll, true)
  window.removeEventListener('resize', hide)
})
</script>

<template>
  <button v-bind="$attrs" ref="trigger" type="button" class="tt" :class="{ dotted }" :aria-label="label" :aria-describedby="open ? id : undefined" :aria-expanded="open"
          @pointerenter="onEnter" @pointerleave="onLeave" @pointerdown="onDown" @click="onClick" @focus="onFocus" @blur="hide">
    <slot />
  </button>
  <Teleport to="body">
    <Transition name="tip-pop">
      <div v-if="open" :id="id" ref="pop" class="tip-pop" :class="place.side" role="tooltip" :style="{ left: place.x + 'px', top: place.y + 'px' }">
        <b v-if="tip.title" class="tt-title">{{ tip.title }}</b>
        <p v-if="tip.text" class="tt-text">{{ tip.text }}</p>
        <dl v-if="tip.rows?.length" class="tt-rows">
          <template v-for="r in tip.rows" :key="r.label">
            <dt>{{ r.label }}</dt>
            <dd>{{ r.value }}</dd>
          </template>
        </dl>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.tt { position: relative; display: inline-flex; align-items: center; max-width: 100%; margin: 0; padding: 0; border: 0; border-radius: var(--radius-sm); background: none; color: inherit; font: inherit; letter-spacing: inherit; text-align: inherit; cursor: help; -webkit-tap-highlight-color: transparent; }
.tt::after { content: ""; position: absolute; inset: -4px; }        /* зона касания шире чипа */
.tt:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.tt.dotted { text-decoration: underline dotted var(--line2); text-decoration-thickness: 1px; text-underline-offset: 4px; }
@media (hover: hover) and (pointer: fine) {
  .tt.dotted:hover { text-decoration-color: var(--muted); }
}
</style>

<style>
/* Окно подсказки живёт в body, поэтому стиль не scoped. */
.tip-pop { position: fixed; z-index: 60; width: max-content; max-width: min(280px, calc(100vw - 16px)); padding: var(--space-2) var(--space-3); border: 1px solid var(--line2); border-radius: var(--radius-sm); background: var(--panel2); box-shadow: var(--lift); color: var(--text); font-size: 12px; line-height: 1.45; pointer-events: none; }
.tip-pop .tt-title { display: block; font-size: 12px; font-weight: 600; }
.tip-pop .tt-text { margin: 2px 0 0; color: var(--muted); }
.tip-pop .tt-title:only-child { margin: 0; }
.tip-pop .tt-rows { display: grid; grid-template-columns: auto 1fr; gap: 2px var(--space-3); margin: var(--space-2) 0 0; }
.tip-pop .tt-rows:first-child { margin-top: 0; }
.tip-pop .tt-rows dt { color: var(--muted); }
.tip-pop .tt-rows dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
.tip-pop-enter-active { transition: opacity var(--dur-fast) var(--ease-out), translate var(--dur-fast) var(--ease-out); }
.tip-pop-leave-active { transition: opacity 90ms ease; }
.tip-pop-enter-from, .tip-pop-leave-to { opacity: 0; }
.tip-pop.below.tip-pop-enter-from { translate: 0 -4px; }
.tip-pop.above.tip-pop-enter-from { translate: 0 4px; }
@media (prefers-reduced-motion: reduce) {
  .tip-pop.below.tip-pop-enter-from, .tip-pop.above.tip-pop-enter-from { translate: none; }
}
</style>
