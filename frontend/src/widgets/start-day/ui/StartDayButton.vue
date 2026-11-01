<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'   // ref: поле сети нейронов
import { useActionStore } from '@/entities/action'
import { useControlStore } from '@/entities/control'
import { UiNeuralField, type NeuralPoint } from '@/shared/ui'
import StartDayModal from './StartDayModal.vue'

/**
 * Кнопка управления днём в шапке: «Старт торговли» когда день не идёт, «Запуск…» и «Торговля идёт» по состоянию платформы (тема ctl); недоступна, если ctl не отвечает
 * или управление выключено, и объясняет почему. Нажатие открывает модалку (запуск, ход запуска, остановка дня). Команды только paper, testnet, observe.
 * Отладка без сервера: ?start=mock (ход запуска), ?start=mock-pick (выбор режима, запуск через общий диалог), ?start=mock-done.
 * Знак состояния: треугольник «старт» до запуска; во время запуска, завершения и пока день идёт это нейрон-маяк (точка и расходящийся сигнал), часть той же сети, что живёт на фоне.
 */
/** compact: колонка свёрнута, остаётся значок состояния; название и причина в подсказке и для скринридера. */
defineProps<{ compact?: boolean }>()
const control = useControlStore()
const open = computed({ get: () => control.dialogOpen, set: (v: boolean) => { control.dialogOpen = v } })        // окно общее: его открывает и страница «Управление»
const ICON = {
  start: 'M8 5.5v13l10.5-6.5z',
  blocked: 'M7 7h10v10H7z',
  unavailable: 'M6 12h12',
} as const
onMounted(() => {
  control.start()
  const q = new URLSearchParams(location.search)
  if (q.has('start')) {            // ?start открывает окно сразу; ?start=mock|mock-pick|mock-done показывает запуск, проверки или итог без сервера (для скриншотов)
    const m = q.get('start')
    if (m === 'mock' || m === 'mock-pick' || m === 'mock-done') {
      control.mock(m === 'mock-pick' ? 'pick' : m === 'mock-done' ? 'done' : 'launch')
      useActionStore().mockEdit(false)        // запуск идёт через общий диалог «Принимаю риски»: ответы сервера изображает движок, на сервер ничего не уходит
    }
    open.value = true
  }
})

const btn = computed(() => control.button)
/** Идущий день называет, что именно идёт: торговля или только наблюдение (по флагам платформы). */
const runningLabel = computed(() => {
  const p = control.ctl?.platform
  return p?.trading ? 'Торговля идёт' : p?.observe ? 'Наблюдение идёт' : 'День идёт'
})
const label = computed(() => ({ start: 'Старт торговли', starting: 'Запуск…', running: runningLabel.value, closing: 'Завершение…', blocked: 'Остановлено', unavailable: 'Старт недоступен' })[btn.value.kind])
const disabled = computed(() => btn.value.kind === 'unavailable' || btn.value.kind === 'blocked')
const beacon = computed(() => btn.value.kind === 'running' || btn.value.kind === 'starting' || btn.value.kind === 'closing')
const tip = computed(() => {
  if (btn.value.why) return `${label.value}: ${btn.value.why}`
  if (btn.value.kind === 'running') return `${label.value}. Нажмите, чтобы посмотреть день или остановить его`
  return label.value
})

/** Живая сеть на кнопке: во время запуска и закрытия думает (частые каскады), в остальные дни тихо живёт; на недоступной и остановленной замирает. */
const field = ref<InstanceType<typeof UiNeuralField> | null>(null)
const NODES: NeuralPoint[] = [[10, 16], [24, 34], [40, 13], [60, 37], [84, 7], [116, 38], [140, 9], [160, 35], [176, 14], [190, 31]]       // широкая кнопка 200×44: нейроны к краям, середина свободна под подпись
const NODES_COMPACT: NeuralPoint[] = [[9, 12], [24, 6], [39, 12], [8, 34], [24, 42], [40, 35]]                                              // квадрат 48×48
const fieldState = computed<'idle' | 'thinking' | 'still'>(() => (disabled.value ? 'still' : btn.value.kind === 'starting' || btn.value.kind === 'closing' ? 'thinking' : 'idle'))

function toggle(ev?: MouseEvent): void {
  if (disabled.value) return
  field.value?.burst(ev)
  if (!open.value && (control.phase === 'done' || control.phase === 'failed')) control.reset()      // новое открытие показывает текущее состояние, а не прошлый итог
  open.value = !open.value
}
</script>

<template>
  <button type="button" class="start" :class="[`k-${btn.kind}`, { compact }]" :aria-disabled="disabled" :aria-haspopup="'dialog'" :aria-label="compact ? label : undefined"
          :title="compact || btn.why ? tip : undefined" @click="toggle" @pointermove="field?.pointer($event)" @pointerleave="field?.pointer(null)">
    <UiNeuralField :key="compact ? 'c' : 'w'" ref="field" class="net" :nodes="compact ? NODES_COMPACT : NODES" :width="compact ? 48 : 200" :height="compact ? 48 : 44" :scale="compact ? 1.1 : 2.2"
                   :link-max="compact ? 22 : 46" :max-deg="3" :state="fieldState" :dot="compact ? 1 : 1.25" :ink="btn.kind === 'running' ? 1.5 : 1.9" />
    <span class="face">
      <span v-if="beacon" class="beacon" aria-hidden="true" />
      <svg v-else class="ico" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path :d="ICON[btn.kind as keyof typeof ICON]" /></svg>
      <span v-if="!compact" class="text">{{ label }}</span>
    </span>
  </button>
  <Teleport to="body">
    <Transition name="sd">
      <StartDayModal v-if="open" @close="open = false" />
    </Transition>
  </Teleport>
</template>

<style scoped>
/* кнопка в боковой колонке: на всю ширину, высота пункта меню; свёрнутая: значок по центру */
/* главное действие панели: сплошная акцентная кнопка; идущий день спокойнее: поверхность панели, тон состояния только в рамке, маяке и сети, подпись цветом текста */
.start { display: flex; align-items: center; justify-content: center; gap: 10px; width: 100%; height: 44px; padding: 0 12px; background: var(--accent); color: var(--bg); border: 1px solid transparent; border-radius: 12px; box-shadow: 0 1px 0 rgba(255, 255, 255, 0.12) inset, 0 4px 14px color-mix(in srgb, var(--accent) 32%, transparent); font: inherit; font-size: 14px; font-weight: 650; white-space: nowrap; cursor: pointer; transition: background-color var(--dur-fast) ease, color var(--dur-fast) ease, border-color var(--dur-fast) ease, box-shadow var(--dur-fast) ease, filter var(--dur-fast) ease, transform 120ms var(--ease-out); }
.start { position: relative; overflow: hidden; isolation: isolate; }
.start.compact { width: 48px; padding: 0; }
.start:active { transform: scale(0.97); }

/* Живая сеть за подписью. Под подписью сеть притушена маской (к краям в полную силу), поэтому связи не идут поперёк букв, а подпись читается на любой заливке. */
.net { position: absolute; inset: 0; z-index: -1; color: color-mix(in srgb, #fff 78%, var(--accent)); }
.start:not(.compact) .net {
  -webkit-mask-image: linear-gradient(90deg, #000 0%, #000 13%, rgba(0, 0, 0, 0.14) 30%, rgba(0, 0, 0, 0.14) 70%, #000 87%, #000 100%);
  mask-image: linear-gradient(90deg, #000 0%, #000 13%, rgba(0, 0, 0, 0.14) 30%, rgba(0, 0, 0, 0.14) 70%, #000 87%, #000 100%);
}
.k-running .net { color: var(--pos); }
.k-blocked .net { color: var(--neg); }
.k-unavailable .net { color: var(--muted); opacity: 0.5; }

.face { display: flex; align-items: center; justify-content: center; gap: 10px; min-width: 0; }
.ico { flex: none; }
.text { overflow: hidden; text-overflow: ellipsis; }

/* Нейрон-маяк: точка и сигнал, который расходится от неё и гаснет. Пока день идёт, сигнал редкий (живой, но не зовёт); при запуске и завершении частый, как и сеть вокруг. */
.beacon { position: relative; flex: none; width: 8px; height: 8px; border-radius: 50%; background: currentColor; }
.beacon::after { content: ''; position: absolute; inset: -1px; border-radius: 50%; border: 1.5px solid currentColor; opacity: 0; animation: sd-signal 3.2s var(--ease-out) infinite; }
.k-starting .beacon::after, .k-closing .beacon::after { animation-duration: 1.2s; }
@keyframes sd-signal {
  0% { transform: scale(1); opacity: 0.85; }
  45%, 100% { transform: scale(2.6); opacity: 0; }
}

/* Идущий день: не главное действие, а состояние. Поверхность панели с лёгким тоном, подпись цветом текста (контраст как у основного текста), зелёные только маяк, рамка и сеть. */
.k-running { background: color-mix(in srgb, var(--pos) 7%, var(--panel)); color: var(--text); border-color: color-mix(in srgb, var(--pos) 38%, var(--line)); box-shadow: none; }
.k-running .beacon { color: var(--pos); }
.k-blocked { background: color-mix(in srgb, var(--neg) 10%, var(--panel)); color: var(--neg); border-color: color-mix(in srgb, var(--neg) 40%, transparent); box-shadow: none; }
.k-unavailable { background: var(--panel2); color: var(--muted); border-color: var(--line2); box-shadow: none; cursor: not-allowed; font-weight: 400; }
@media (hover: hover) and (pointer: fine) {
  .k-start:hover, .k-starting:hover, .k-closing:hover { filter: brightness(1.08); }
  .k-running:hover { background: color-mix(in srgb, var(--pos) 12%, var(--panel)); border-color: color-mix(in srgb, var(--pos) 55%, var(--line)); }
}
.start:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
@media (prefers-reduced-motion: reduce) {
  .beacon::after { display: none; }
  .beacon { box-shadow: 0 0 0 3px color-mix(in srgb, currentColor 24%, transparent); }        /* без движения: точка с ореолом, «идёт» видно и так */
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
