<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useControlStore } from '@/entities/control'
import StartDayModal from './StartDayModal.vue'

/**
 * Кнопка управления днём в шапке: «Старт торговли» когда день не идёт, «Запуск…» и «День идёт» по состоянию платформы (тема ctl); недоступна, если ctl не отвечает
 * или управление выключено, и объясняет почему. Нажатие открывает модалку (запуск, ход запуска, остановка дня). Команды только paper, testnet, observe.
 */
const control = useControlStore()
const open = ref(false)
onMounted(() => {
  control.start()
  const q = new URLSearchParams(location.search)
  if (q.has('start')) {            // ?start открывает окно сразу; ?start=mock|mock-pick|mock-done показывает запуск, проверки или итог без сервера (для скриншотов)
    const m = q.get('start')
    if (m === 'mock' || m === 'mock-pick' || m === 'mock-done') control.mock(m === 'mock-pick' ? 'pick' : m === 'mock-done' ? 'done' : 'launch')
    open.value = true
  }
})

const btn = computed(() => control.button)
const LABEL = { start: 'Старт торговли', starting: 'Запуск…', running: 'День идёт', closing: 'Завершение…', blocked: 'Остановлено', unavailable: 'Старт недоступен' } as const
const disabled = computed(() => btn.value.kind === 'unavailable' || btn.value.kind === 'blocked')

function toggle(): void {
  if (disabled.value) return
  if (!open.value && (control.phase === 'done' || control.phase === 'failed')) control.reset()      // новое открытие показывает текущее состояние, а не прошлый итог
  open.value = !open.value
}
</script>

<template>
  <button type="button" class="start" :class="`k-${btn.kind}`" :aria-disabled="disabled" :aria-haspopup="'dialog'" :title="btn.why ?? undefined" @click="toggle">
    <i class="dot" aria-hidden="true" />{{ LABEL[btn.kind] }}
  </button>
  <Teleport to="body">
    <Transition name="sd">
      <StartDayModal v-if="open" @close="open = false" />
    </Transition>
  </Teleport>
</template>

<style scoped>
.start { display: inline-flex; align-items: center; gap: var(--space-2); padding: 5px 14px; background: var(--accent-bg); color: var(--accent); border: 1px solid var(--accent); border-radius: var(--radius-sm); font: inherit; font-size: 13px; font-weight: 600; cursor: pointer; transition: background-color var(--dur-fast) ease, color var(--dur-fast) ease, border-color var(--dur-fast) ease, transform 120ms var(--ease-out); }
.start:active { transform: scale(0.97); }
.dot { width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
.k-starting .dot, .k-closing .dot { animation: st-pulse 1.2s var(--ease-in-out) infinite; }
.k-running { background: transparent; color: var(--pos); border-color: var(--pos); }
.k-blocked { background: transparent; color: var(--neg); border-color: var(--neg); }
.k-unavailable { background: transparent; color: var(--muted); border-color: var(--line2); cursor: not-allowed; font-weight: 400; }
@keyframes st-pulse { 50% { opacity: 0.3; } }
@media (hover: hover) and (pointer: fine) {
  .k-start:hover, .k-starting:hover, .k-closing:hover { background: color-mix(in srgb, var(--accent) 22%, transparent); }
  .k-running:hover { background: color-mix(in srgb, var(--pos) 12%, transparent); }
}
.start:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
@media (prefers-reduced-motion: reduce) {
  .k-starting .dot, .k-closing .dot { animation: none; }
  .start:active { transform: none; }
}
</style>

<style>
/* окно в body (Teleport): стили перехода не scoped. Открытие редкое: короткий выезд панели, затемнение; закрытие быстрее, без движения */
.sd-enter-from, .sd-leave-to { opacity: 0; }
.sd-enter-active { transition: opacity var(--dur-enter) var(--ease-out); }
.sd-leave-active { transition: opacity 120ms var(--ease-out); }
.sd-enter-from .modal { transform: translateY(10px) scale(0.97); }
.sd-enter-active .modal { transition: transform 240ms var(--ease-out); }
@media (prefers-reduced-motion: reduce) {
  .sd-enter-from .modal { transform: none; }
}
</style>
