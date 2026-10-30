<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useControlStore } from '@/entities/control'
import StartDayModal from './StartDayModal.vue'

/**
 * Кнопка управления днём в шапке: «Старт торговли» когда день не идёт, «Запуск…» и «День идёт» по состоянию платформы (тема ctl); недоступна, если ctl не отвечает
 * или управление выключено, и объясняет почему. Нажатие открывает модалку (запуск, ход запуска, остановка дня). Команды только paper, testnet, observe.
 */
/** compact: колонка свёрнута, остаётся значок состояния; название и причина в подсказке и для скринридера. */
defineProps<{ compact?: boolean }>()
const control = useControlStore()
const open = ref(false)
const ICON = {
  start: 'M8 5.5v13l10.5-6.5z',
  starting: 'M8 5.5v13l10.5-6.5z',
  running: 'M5 12.5l4.5 4.5L19 7.5',
  closing: 'M8 5.5v13l10.5-6.5z',
  blocked: 'M7 7h10v10H7z',
  unavailable: 'M6 12h12',
} as const
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
  <button type="button" class="start" :class="[`k-${btn.kind}`, { compact }]" :aria-disabled="disabled" :aria-haspopup="'dialog'" :aria-label="compact ? LABEL[btn.kind] : undefined"
          :title="compact ? (btn.why ? `${LABEL[btn.kind]}: ${btn.why}` : LABEL[btn.kind]) : (btn.why ?? undefined)" @click="toggle">
    <svg class="ico" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path :d="ICON[btn.kind]" /></svg>
    <span v-if="!compact" class="text">{{ LABEL[btn.kind] }}</span>
  </button>
  <Teleport to="body">
    <Transition name="sd">
      <StartDayModal v-if="open" @close="open = false" />
    </Transition>
  </Teleport>
</template>

<style scoped>
/* кнопка в боковой колонке: на всю ширину, высота пункта меню; свёрнутая: значок по центру */
/* главное действие панели: сплошная акцентная кнопка (как «Старт» на старом дашборде); идущий день, остановка и недоступность спокойнее: тон по состоянию, без заливки */
.start { display: flex; align-items: center; justify-content: center; gap: 10px; width: 100%; height: 44px; padding: 0 12px; background: var(--accent); color: var(--bg); border: 1px solid transparent; border-radius: 12px; box-shadow: 0 1px 0 rgba(255, 255, 255, 0.12) inset, 0 4px 14px color-mix(in srgb, var(--accent) 32%, transparent); font: inherit; font-size: 14px; font-weight: 650; white-space: nowrap; cursor: pointer; transition: background-color var(--dur-fast) ease, color var(--dur-fast) ease, border-color var(--dur-fast) ease, box-shadow var(--dur-fast) ease, filter var(--dur-fast) ease, transform 120ms var(--ease-out); }
.start.compact { width: 48px; padding: 0; }
.start:active { transform: scale(0.97); }
.ico { flex: none; }
.text { overflow: hidden; text-overflow: ellipsis; }
.k-starting .ico, .k-closing .ico { animation: st-pulse 1.2s var(--ease-in-out) infinite; }
.k-running { background: color-mix(in srgb, var(--pos) 14%, var(--panel)); color: var(--pos); border-color: color-mix(in srgb, var(--pos) 40%, transparent); box-shadow: none; }
.k-blocked { background: color-mix(in srgb, var(--neg) 12%, var(--panel)); color: var(--neg); border-color: color-mix(in srgb, var(--neg) 40%, transparent); box-shadow: none; }
.k-unavailable { background: var(--panel2); color: var(--muted); border-color: var(--line2); box-shadow: none; cursor: not-allowed; font-weight: 400; }
@keyframes st-pulse { 50% { opacity: 0.3; } }
@media (hover: hover) and (pointer: fine) {
  .k-start:hover, .k-starting:hover, .k-closing:hover { filter: brightness(1.08); }
  .k-running:hover { background: color-mix(in srgb, var(--pos) 22%, var(--panel)); }
}
.start:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
@media (prefers-reduced-motion: reduce) {
  .k-starting .ico, .k-closing .ico { animation: none; }
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
