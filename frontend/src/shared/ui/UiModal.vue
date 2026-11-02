<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { focusables, wrapTarget } from './modal'

/**
 * Модальное окно для форм: поверх страницы, фокус заперт внутри (Tab и Shift+Tab ходят по кругу), Esc и крестик закрывают, по затемнению окно не закрывается
 * (в форме может быть набранное). При открытии фокус на рамке окна (диктор читает заголовок), при закрытии возвращается на кнопку, которая окно открыла.
 * Единый диалог «Принимаю риски» стоит выше (z-index 70) и сам перехватывает Esc и Tab, пока открыт. Слоты: title (заголовок), по умолчанию (прокручиваемое тело), footer (кнопки внизу).
 * Движение: окно появляется за 200 мс (прозрачность и сдвиг на 8 px), исчезает за 120 мс только прозрачностью; высота не анимируется. При prefers-reduced-motion остаётся прозрачность.
 */
const props = defineProps<{ open: boolean; label: string }>()
const emit = defineEmits<{ close: [] }>()
const dialog = ref<HTMLElement | null>(null)
const uid = `um${Math.random().toString(36).slice(2, 8)}`
let opener: HTMLElement | null = null

function onKey(ev: KeyboardEvent): void {
  if (ev.key === 'Escape' && !ev.defaultPrevented) {
    ev.preventDefault()
    emit('close')
  }
}
function onTab(ev: KeyboardEvent): void {
  if (ev.key !== 'Tab' || !dialog.value) return
  const to = wrapTarget(focusables(dialog.value), document.activeElement, dialog.value, ev.shiftKey)
  if (to) {
    ev.preventDefault()
    to.focus()
  }
}

watch(() => props.open, (v) => {
  if (v) {
    const a = document.activeElement as HTMLElement | null
    opener = a && a !== document.body ? a : null
    document.addEventListener('keydown', onKey)
    void nextTick(() => dialog.value?.focus({ preventScroll: true }))
  } else {
    document.removeEventListener('keydown', onKey)
    const back = opener
    opener = null
    void nextTick(() => back?.isConnected && back.focus?.({ preventScroll: true }))
  }
}, { immediate: true })
onBeforeUnmount(() => document.removeEventListener('keydown', onKey))
</script>

<template>
  <Teleport to="body">
    <Transition name="um" appear>
      <div v-if="open" class="um">
        <div ref="dialog" class="box" role="dialog" aria-modal="true" :aria-labelledby="uid" tabindex="-1" @keydown="onTab">
          <header class="head">
            <h2 :id="uid" class="ttl">{{ label }}</h2>
            <slot name="chip" />
            <button type="button" class="x" aria-label="Закрыть" @click="emit('close')"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg></button>
          </header>
          <div class="body"><slot /></div>
          <footer v-if="$slots.footer" class="foot"><slot name="footer" /></footer>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.um { position: fixed; inset: 0; z-index: 56; display: grid; place-items: center; padding: var(--space-4); background: rgba(5, 9, 14, 0.66); }
.box { width: min(640px, 100%); max-height: calc(100dvh - 2 * var(--space-4)); display: flex; flex-direction: column; min-height: 0; background: var(--panel); border: 1px solid var(--line2); border-radius: 16px; box-shadow: 0 24px 64px rgba(0, 0, 0, 0.5); outline: none; }
.head { flex: none; display: flex; align-items: center; gap: var(--space-3); padding: var(--space-4) var(--space-4) var(--space-3) var(--space-5); border-bottom: 1px solid var(--line); }
.ttl { flex: 0 1 auto; min-width: 0; margin: 0; font-size: 17px; font-weight: 650; letter-spacing: -0.01em; line-height: 1.3; overflow-wrap: anywhere; }
.x { flex: none; margin-left: auto; display: grid; place-items: center; width: 40px; height: 40px; border: 0; border-radius: var(--radius-sm); background: none; color: var(--muted); cursor: pointer; transition: color var(--dur-fast) ease, background-color var(--dur-fast) ease; }
.x:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.body { flex: 1 1 auto; min-height: 0; overflow-y: auto; overscroll-behavior: contain; padding: var(--space-4) var(--space-5); display: grid; gap: 0; align-content: start; }
.foot { flex: none; display: flex; justify-content: flex-end; flex-wrap: wrap; gap: var(--space-2); padding: var(--space-3) var(--space-5) var(--space-4); border-top: 1px solid var(--line); }
@media (max-width: 560px) {
  .um { padding: var(--space-2); }
  .box { max-height: calc(100dvh - 2 * var(--space-2)); }
  .head { padding-left: var(--space-4); }
  .body { padding: var(--space-4); }
  .foot { padding: var(--space-3) var(--space-4); }
  .foot :deep(button) { flex: 1 1 auto; }
}
@media (hover: hover) and (pointer: fine) {
  .x:hover { color: var(--text); background: var(--panel2); }
}
.um-enter-active, .um-enter-active .box { transition: opacity var(--dur-enter) var(--ease-out), transform var(--dur-enter) var(--ease-out); }
.um-enter-from { opacity: 0; }
.um-enter-from .box { opacity: 0; transform: translateY(8px); }
.um-leave-active { transition: opacity 120ms var(--ease-out); }
.um-leave-to { opacity: 0; }
@media (prefers-reduced-motion: reduce) {
  .um-enter-from .box { transform: none; }
}
</style>
