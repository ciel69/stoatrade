<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch, watchEffect } from 'vue'
import { acceptLabel, blockReasons, isExpired, passedGates, secondsLeft, useActionStore, type ActionMock, type ActionPhase, type RiskItem } from '@/entities/action'

/**
 * Единое подтверждение рисков для любого действия управления и уведомления о безрисковых.
 * Безрисковое действие окна не открывает: команда уходит сразу, итог приходит уведомлением справа внизу. Рискованное открывает одно и то же окно «Принимаю риски»:
 * риски списком (важное сверху), выполненные условия под сворачиванием, одна главная кнопка. Фразы подтверждения и nonce интерфейс возвращает в команду сам.
 * Окно монтируется один раз в app/App.vue. Отладка без сервера: ?action=mock-confirm|mock-blocked|mock-done|mock-fail|mock-toast|mock-full.
 */
const action = useActionStore()

const CHECK_DELAY_MS = 300         // ответ быстрее: окно не показываем, безрисковое действие не должно мигать диалогом
const DONE_CLOSE_MS = 2500
const TOAST_MS = { ok: 4000, bad: 8000 }

// ---- что показывать
const checkShown = ref(false)
let checkTimer: ReturnType<typeof setTimeout> | undefined
watch(() => action.phase, (p) => {
  clearTimeout(checkTimer)
  checkShown.value = false
  if (p === 'checking') checkTimer = setTimeout(() => { checkShown.value = true }, CHECK_DELAY_MS)
}, { immediate: true })

const visible = computed(() => {
  const p = action.phase
  if (p === 'confirm' || p === 'blocked') return true
  if (p === 'checking') return action.riskyRun === true || checkShown.value
  if (p === 'sending' || p === 'waiting' || p === 'done' || p === 'failed') return action.riskyRun === true
  return false
})
/** Безрисковая команда в пути: окна нет, но выполнение не должно быть невидимым. */
const busy = computed(() => !visible.value && (action.phase === 'sending' || action.phase === 'waiting'))

/** Снимок для показа: при закрытии окно успевает плавно исчезнуть с прежним содержимым, а не пустым. */
interface View { phase: ActionPhase; title: string; what: string; risks: RiskItem[]; passed: Array<{ id: string; text: string }>; reasons: string[]; text: string }
const view = ref<View>({ phase: 'idle', title: '', what: '', risks: [], passed: [], reasons: [], text: '' })
watchEffect(() => {
  if (action.phase === 'idle') return
  view.value = {
    phase: action.phase,
    title: action.spec?.title ?? '',
    what: action.spec?.what ?? '',
    risks: action.risks,
    passed: passedGates(action.challenge).map((g) => ({ id: g.id, text: [g.name, g.msg].filter(Boolean).join(': ') || g.id })),
    reasons: action.phase === 'blocked' ? blockReasons(action.challenge, action.error ?? '') : [],
    text: action.phase === 'done' ? action.resultText : (action.error ?? ''),
  }
})

// ---- пауза и срок
const now = ref(Date.now())
let tick: ReturnType<typeof setInterval> | undefined
const left = computed(() => secondsLeft(action.challenge?.min_delay, action.receivedAt, now.value))
const expired = computed(() => action.phase === 'confirm' && isExpired(action.challenge?.expires_in, action.receivedAt, now.value))
watch(() => action.phase === 'confirm', (on) => {
  clearInterval(tick)
  if (!on) return
  now.value = Date.now()
  tick = setInterval(() => { now.value = Date.now() }, 250)
}, { immediate: true })

const details = ref(false)
watch(visible, (v) => { if (v) details.value = false })

// ---- закрытие и клавиатура
const dialog = ref<HTMLElement | null>(null)
let opener: HTMLElement | null = null
let closeTimer: ReturnType<typeof setTimeout> | undefined
const holding = ref(false)

function dismiss(): void {
  const p = action.phase
  if (p === 'checking' || p === 'confirm' || p === 'blocked') action.cancel()
  else if (p === 'done' || p === 'failed') action.reset()
  // sending, waiting: команда уже у сервера, окно закрыть нельзя, пока не придёт итог
}
function onKey(ev: KeyboardEvent): void {
  if (ev.key === 'Escape') {
    ev.preventDefault()
    ev.stopPropagation()
    dismiss()
    return
  }
  if (ev.key !== 'Tab' || !dialog.value) return
  const els = [...dialog.value.querySelectorAll<HTMLElement>('button:not([disabled]), a[href], [tabindex="0"]')].filter((e) => e.offsetParent !== null)
  if (!els.length) { ev.preventDefault(); dialog.value.focus(); return }
  const first = els[0]
  const last = els[els.length - 1]
  const act = document.activeElement
  if (ev.shiftKey && (act === first || act === dialog.value)) { ev.preventDefault(); last.focus() }
  else if (!ev.shiftKey && (act === last || act === dialog.value)) { ev.preventDefault(); first.focus() }
}
function focusMain(): void {
  nextTick(() => (dialog.value?.querySelector<HTMLElement>('[data-autofocus]') ?? dialog.value)?.focus())
}
function armClose(ms: number): void {
  clearTimeout(closeTimer)
  closeTimer = setTimeout(() => { if (holding.value) return; if (action.phase === 'done') action.reset() }, ms)
}
function release(): void {
  holding.value = false
  if (action.phase === 'done') armClose(1200)
}

watch(visible, (v) => {
  if (v) {
    opener = document.activeElement as HTMLElement | null
    document.addEventListener('keydown', onKey, true)
    focusMain()
  } else {
    document.removeEventListener('keydown', onKey, true)
    holding.value = false
    opener?.focus?.()
    opener = null
  }
})
watch(() => action.phase, (p) => {
  clearTimeout(closeTimer)
  if (visible.value) focusMain()
  if (p === 'done' && action.riskyRun === true) armClose(DONE_CLOSE_MS)
})

// ---- уведомления: каждое исчезает само
const timers = new Map<number, ReturnType<typeof setTimeout>>()
watch(() => action.toasts, (list) => {
  for (const t of list) {
    if (timers.has(t.id)) continue
    timers.set(t.id, setTimeout(() => { timers.delete(t.id); action.dismissToast(t.id) }, TOAST_MS[t.tone]))
  }
}, { deep: true, immediate: true })

onMounted(() => {
  action.start()
  const m = new URLSearchParams(location.search).get('action')
  if (m && m.startsWith('mock-')) action.mock(m.slice(5) as ActionMock)
})
onUnmounted(() => {
  document.removeEventListener('keydown', onKey, true)
  clearTimeout(checkTimer)
  clearTimeout(closeTimer)
  clearInterval(tick)
  timers.forEach((t) => clearTimeout(t))
})
</script>

<template>
  <Teleport to="body">
    <Transition name="ad">
      <div v-if="visible" class="backdrop" @pointerdown.self="view.phase !== 'confirm' && dismiss()">
        <div ref="dialog" class="modal" role="dialog" aria-modal="true" aria-labelledby="ad-title" tabindex="-1" @pointerenter="holding = true" @pointerleave="release">
          <h2 id="ad-title" class="title">{{ view.title }}</h2>
          <p v-if="view.what" class="what">{{ view.what }}</p>

          <p v-if="view.phase === 'checking'" class="status" role="status"><i class="spin" aria-hidden="true" />Проверяю условия…</p>

          <template v-if="view.phase === 'confirm'">
            <h3 class="sub">Что нужно принять</h3>
            <ul class="risks">
              <li v-for="r in view.risks" :key="r.id" :class="r.level">
                <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 4l9 16H3z" /><path d="M12 10v4M12 17.2v.01" /></svg>
                <span>{{ r.text }}</span>
              </li>
            </ul>
            <div v-if="view.passed.length" class="cond">
              <button type="button" class="more" :aria-expanded="details" aria-controls="ad-gates" @click="details = !details">
                Выполненные условия: {{ view.passed.length }}
                <svg viewBox="0 0 12 12" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 4.5L6 7.5L9 4.5" /></svg>
              </button>
              <div id="ad-gates" class="fold" :class="{ on: details }">
                <ul class="gates" aria-label="Выполненные условия" :inert="!details">
                  <li v-for="g in view.passed" :key="g.id"><i class="mark" aria-hidden="true" />{{ g.text }}</li>
                </ul>
              </div>
            </div>
            <p v-if="expired" class="fine" role="status">Проверка устарела: условия могли измениться. Проверьте заново.</p>
            <div class="foot">
              <button type="button" class="ghost" data-autofocus @click="action.cancel()">Отмена</button>
              <button v-if="expired" type="button" class="cta" @click="action.recheck()">Проверить заново</button>
              <button v-else type="button" class="cta warn" :disabled="left > 0" @click="action.accept()">{{ acceptLabel(left) }}</button>
            </div>
          </template>

          <template v-else-if="view.phase === 'blocked'">
            <h3 class="sub">Что мешает</h3>
            <ul class="risks">
              <li v-for="(r, i) in view.reasons" :key="i" class="bad">
                <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>
                <span>{{ r }}</span>
              </li>
            </ul>
            <p class="fine">Пока условия не выполнены, действие не выполняется.</p>
            <div class="foot">
              <button type="button" class="ghost" @click="action.recheck()">Проверить заново</button>
              <button type="button" class="cta" data-autofocus @click="action.cancel()">Закрыть</button>
            </div>
          </template>

          <template v-else-if="view.phase === 'sending' || view.phase === 'waiting'">
            <p class="status" role="status"><i class="spin" aria-hidden="true" />{{ view.phase === 'sending' ? 'Отправляю команду…' : 'Команда принята, жду выполнения…' }}</p>
          </template>

          <template v-else-if="view.phase === 'done'">
            <p class="result ok" role="status">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5" /></svg>
              <span><b>Готово</b><em v-if="view.text">{{ view.text }}</em></span>
            </p>
            <div class="foot"><button type="button" class="ghost" data-autofocus @click="action.reset()">Закрыть</button></div>
          </template>

          <template v-else-if="view.phase === 'failed'">
            <p class="result bad" role="alert">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>
              <span><b>Не удалось выполнить</b><em>{{ view.text }}</em></span>
            </p>
            <div class="foot"><button type="button" class="cta" data-autofocus @click="action.reset()">Закрыть</button></div>
          </template>

          <div v-if="view.phase === 'checking'" class="foot"><button type="button" class="ghost" data-autofocus @click="action.cancel()">Отмена</button></div>
        </div>
      </div>
    </Transition>

    <!-- уведомления о безрисковых действиях: над облаком событий -->
    <div class="toasts" aria-live="polite">
      <Transition name="adt">
        <div v-if="busy" class="toast" role="status"><i class="spin" aria-hidden="true" /><div><b>{{ action.spec?.title }}</b><span>Выполняю…</span></div></div>
      </Transition>
      <TransitionGroup name="adt">
        <div v-for="t in action.toasts" :key="t.id" class="toast" :class="t.tone" :role="t.tone === 'bad' ? 'alert' : 'status'">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path v-if="t.tone === 'ok'" d="M5 12.5l4.5 4.5L19 7.5" /><path v-else d="M6 6l12 12M18 6L6 18" /></svg>
          <div><b>{{ t.title }}</b><span>{{ t.text }}</span></div>
          <button type="button" class="tx" aria-label="Закрыть уведомление" @click="action.dismissToast(t.id)">
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>
          </button>
        </div>
      </TransitionGroup>
    </div>
  </Teleport>
</template>

<style scoped>
.backdrop { position: fixed; inset: 0; z-index: 70; display: grid; place-items: center; padding: var(--space-4); background: rgba(5, 9, 14, 0.66); }
.modal { width: min(460px, 100%); max-height: calc(100vh - 2 * var(--space-4)); overflow-y: auto; display: grid; gap: var(--space-3); padding: var(--space-5); background: var(--panel); border: 1px solid var(--line2); border-radius: 16px; box-shadow: 0 24px 64px rgba(0, 0, 0, 0.5); outline: none; }
.title { margin: 0; font-size: 17px; font-weight: 650; letter-spacing: -0.01em; line-height: 1.3; text-wrap: balance; }
.what { margin: -4px 0 0; font-size: 13px; line-height: 1.5; color: var(--muted); }
.sub { margin: var(--space-2) 0 0; font-size: 13px; font-weight: 600; }
.status { margin: 0; display: flex; align-items: center; gap: var(--space-2); font-size: 13px; color: var(--muted); }
.fine { margin: 0; font-size: 12px; line-height: 1.45; color: var(--muted); }

.risks { list-style: none; margin: 0; padding: 0; display: grid; gap: var(--space-2); }
.risks li { display: grid; grid-template-columns: 16px minmax(0, 1fr); align-items: start; gap: var(--space-2); font-size: 13px; line-height: 1.45; }
.risks li svg { margin-top: 2px; }
.risks li.danger svg, .risks li.bad svg { color: var(--neg); }
.risks li.warn svg { color: var(--warn); }
.risks li.amber svg { color: var(--muted); }
.risks li.danger { font-weight: 600; }

.cond { display: grid; }        /* кнопка и список одним блоком: свёрнутый список не добавляет второй отступ перед кнопками */
.more { justify-self: start; display: inline-flex; align-items: center; gap: var(--space-2); margin-left: -8px; padding: 4px 8px; background: transparent; border: 0; border-radius: var(--radius-sm); color: var(--muted); font: inherit; font-size: 12px; cursor: pointer; transition: color var(--dur-fast) ease; }
.more svg { transition: transform var(--dur-enter) var(--ease-out); }
.more[aria-expanded='true'] svg { transform: rotate(180deg); }
.fold { display: grid; grid-template-rows: 0fr; transition: grid-template-rows var(--dur-enter) var(--ease-out); }
.fold.on { grid-template-rows: 1fr; }
.gates { list-style: none; margin: 0; padding: 0; min-height: 0; overflow: hidden; display: grid; gap: 4px; font-size: 12px; color: var(--muted); }
.fold.on > .gates { padding-top: 2px; }
.gates li { display: grid; grid-template-columns: 7px minmax(0, 1fr); align-items: center; gap: var(--space-2); }
.mark { width: 7px; height: 7px; border-radius: 50%; background: var(--pos); }

.result { margin: 0; display: grid; grid-template-columns: 18px minmax(0, 1fr); gap: var(--space-2); align-items: start; font-size: 13px; line-height: 1.45; }
.result svg { margin-top: 1px; }
.result.ok svg { color: var(--pos); }
.result.bad svg { color: var(--neg); }
.result b { display: block; font-weight: 650; }
.result em { display: block; font-style: normal; color: var(--muted); overflow-wrap: anywhere; }

.foot { display: flex; align-items: center; justify-content: flex-end; flex-wrap: wrap; gap: var(--space-2); margin-top: var(--space-2); }
.cta, .ghost { min-height: 40px; padding: 0 var(--space-4); border-radius: 10px; font: inherit; font-size: 13px; cursor: pointer; transition: filter var(--dur-fast) ease, opacity var(--dur-fast) ease, color var(--dur-fast) ease, border-color var(--dur-fast) ease, transform 120ms var(--ease-out); }
.cta { background: var(--accent); color: var(--bg); border: 0; font-weight: 650; }
.cta.warn { background: var(--warn); font-variant-numeric: tabular-nums; }
.cta:disabled { opacity: 0.5; cursor: not-allowed; }
.cta:active:not(:disabled) { transform: scale(0.98); }
.ghost { background: transparent; color: var(--muted); border: 1px solid var(--line2); }

.spin { flex: none; width: 12px; height: 12px; border-radius: 50%; border: 2px solid var(--line2); border-top-color: var(--accent); animation: ad-spin 0.9s linear infinite; }
@keyframes ad-spin { to { transform: rotate(360deg); } }

/* окно: редкое событие, короткий выезд панели; закрытие быстрее и без движения */
.ad-enter-from, .ad-leave-to { opacity: 0; }
.ad-enter-active { transition: opacity var(--dur-enter) var(--ease-out); }
.ad-leave-active { transition: opacity 120ms var(--ease-out); }
.ad-enter-from .modal { transform: translateY(8px) scale(0.98); }
.ad-enter-active .modal { transition: transform 240ms var(--ease-out); }

/* уведомления: над облаком событий (кнопка 48px + отступ), новые снизу вверх */
.toasts { position: fixed; right: var(--space-5); bottom: calc(var(--space-5) + 48px + var(--space-3)); z-index: 30; display: flex; flex-direction: column; align-items: flex-end; gap: var(--space-2); pointer-events: none; }
.toast { pointer-events: auto; width: min(360px, calc(100vw - 2 * var(--space-5))); display: flex; align-items: flex-start; gap: var(--space-2); padding: var(--space-3); background: var(--panel); border: 1px solid var(--line2); border-radius: var(--radius); box-shadow: 0 8px 24px rgba(0, 0, 0, 0.28); font-size: 12px; line-height: 1.45; }
.toast > svg { flex: none; margin-top: 1px; }
.toast.ok > svg { color: var(--pos); }
.toast.bad > svg { color: var(--neg); }
.toast .spin { margin-top: 3px; }
.toast > div { flex: 1 1 auto; min-width: 0; }
.toast b { display: block; font-size: 13px; font-weight: 650; }
.toast span { display: block; color: var(--muted); overflow-wrap: anywhere; }
.tx { flex: none; display: grid; place-items: center; width: 24px; height: 24px; margin: -4px -4px 0 0; padding: 0; background: transparent; border: 0; border-radius: var(--radius-sm); color: var(--muted); cursor: pointer; }
.adt-enter-from { opacity: 0; transform: translateY(8px); }
.adt-leave-to { opacity: 0; transform: translateY(8px); }
.adt-enter-active { transition: opacity var(--dur-enter) var(--ease-out), transform var(--dur-enter) var(--ease-out); }
.adt-leave-active { transition: opacity 160ms var(--ease-out), transform 160ms var(--ease-out); }
.adt-move { transition: transform var(--dur-move) var(--ease-in-out); }

@media (hover: hover) and (pointer: fine) {
  .more:hover, .tx:hover { color: var(--text); }
  .ghost:hover { color: var(--text); border-color: var(--muted); }
  .cta:not(:disabled):hover { filter: brightness(1.08); }
}
.cta:focus-visible, .ghost:focus-visible, .more:focus-visible, .tx:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
@media (max-width: 520px) {
  .modal { padding: var(--space-4); }
  .foot { flex-direction: column-reverse; align-items: stretch; }
}
@media (prefers-reduced-motion: reduce) {
  .ad-enter-from .modal, .adt-enter-from, .adt-leave-to { transform: none; }
  .adt-move, .fold, .more svg, .cta { transition: none; }
  .spin { animation: none; border-right-color: var(--accent); }
}
</style>
