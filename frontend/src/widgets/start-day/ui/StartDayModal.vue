<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { dialogUp, ownProgress, unavailableReason, useActionStore } from '@/entities/action'
import {
  CTL_STEP_LABEL, STOP_WHAT, canBegin, canBeginLive, dayArgs, dayWhat, launchFill, liveAccess, liveGateRows, liveGatesBad, liveLimits, litNodes, regionProgress, stepRows, stopArgs, useControlStore, warnCount,
  type StartMode,
} from '@/entities/control'
import { BRAIN_REGIONS, UiBrainIcon, UiMeter, UiSegment, type BrainIconState } from '@/shared/ui'
import { LIVE_CONFIRM_TITLE, LIVE_GATES_NOTE, LIVE_GATES_TITLE, LIVE_INTRO, LIVE_PICK, LIVE_START_BUTTON, LIVE_TITLE, LIVE_WARN_NOTE, LIVE_WHAT, dayLabel, failureText, humanize, modeName, plainify, useNow } from '@/shared/lib'

/**
 * Старт торгового дня. Компактная карточка по центру: крупный мозг, под ним одно главное сообщение, одна тонкая шкала хода запуска и одна кнопка.
 * Детали (проверки готовности, шаги запуска) спрятаны под «Подробнее» и раскрываются сами, только если что-то не прошло. Области коры зажигаются по реальным шагам ctl,
 * на каждом новом шаге по сети идёт волна, в конце мозг оживает. Режимы: бумага, тестнет, наблюдение и «Реальные деньги».
 * Режим выбирается здесь, а подтверждение рисков делает общее окно «Принимаю риски» (виджет action-dialog): предупреждения и жёлтые проверки там, без отдельных галочек.
 * Если предупреждений нет, день стартует сразу. Жёсткие условия (ворота) проверяет ctl: пока они не выполнены, «Начать торговлю» недоступна.
 * Реальные деньги: вариант виден всегда, но выбираем только когда сервер разрешает (расширенное управление и выключатель владельца), иначе он серый и причина написана словами.
 * Для них показаны усиленный текст, жёсткие проверки H1–H8 словами со статусом и пределы из ответа платформы; подтверждение то же единое окно, но со своим заголовком про реальные деньги.
 * Отладка без сервера: ?start=mock-live | mock-live-blocked | mock-live-off.
 */
const emit = defineEmits<{ close: [] }>()
const control = useControlStore()
const action = useActionStore()
const now = useNow()

/** Реальные деньги доступны, только если сервер это разрешает (расширенное управление, выключатель владельца, служба управления на связи). */
const live = computed(() => liveAccess(control.hello, control.ctl))
const PICKS = computed<ReadonlyArray<{ value: StartMode; label: string; muted?: boolean }>>(() => [
  { value: 'paper', label: 'Бумага' }, { value: 'testnet', label: 'Тестнет' }, { value: 'observe', label: 'Наблюдение' }, { value: 'live', label: LIVE_PICK, muted: !live.value.ok },
])
const HINT: Record<string, string> = {
  paper: 'Виртуальные сделки, реальных денег нет.',
  testnet: 'Настоящая биржа, но не настоящие деньги.',
  observe: 'Ничего не торгуется, платформа наблюдает.',
}

const pick = ref<StartMode>(control.mode)
const dialog = ref<HTMLElement | null>(null)
const icon = ref<InstanceType<typeof UiBrainIcon> | null>(null)
const confirmClose = ref(false)
const stopCheck = computed(() => action.spec?.action === 'day.stop' && action.phase === 'checking')
const details = ref(false)
let opener: HTMLElement | null = null

const platform = computed(() => control.ctl?.platform ?? null)
const running = computed(() => platform.value?.state === 'running')
/** Запуск дня идёт через общий движок действий: команда в пути (sending, waiting) считается запуском, не дожидаясь, пока тема ctl покажет «starting». */
const sentByUs = computed(() => action.spec?.action === 'day.start' && (action.phase === 'sending' || action.phase === 'waiting'))
const startedByUs = ref(false)
const launching = computed(() => control.phase === 'starting' || platform.value?.state === 'starting' || sentByUs.value)
/** Остановка идёт через тот же общий движок: команда в пути (sending, waiting) уже считается завершением дня, не дожидаясь, пока тема ctl покажет «closing». */
const stopSent = computed(() => action.spec?.action === 'day.stop' && (action.phase === 'sending' || action.phase === 'waiting'))
const closing = computed(() => stopSent.value || platform.value?.state === 'closing')
const finished = computed(() => startedByUs.value || control.phase === 'done')
const view = computed<'launch' | 'running' | 'closing' | 'done' | 'pick'>(() => (launching.value ? 'launch' : closing.value ? 'closing' : finished.value ? 'done' : running.value ? 'running' : 'pick'))

onMounted(() => {
  opener = document.activeElement as HTMLElement | null
  document.addEventListener('keydown', onKey, true)
  if (view.value === 'pick') onPick(pick.value)
  nextTick(() => (dialog.value?.querySelector<HTMLElement>('[data-autofocus] button, button[data-autofocus]') ?? dialog.value)?.focus())
})
onUnmounted(() => {
  document.removeEventListener('keydown', onKey, true)
  clearTimeout(autoTimer)
  opener?.focus?.()
})

function onPick(v: StartMode): void {
  pick.value = v
  if (v === 'live' && !live.value.ok) {        // недоступно: ни одного запроса к платформе, только причина словами
    control.reset()
    return
  }
  control.check(v)
}

// ---- шаги запуска и мозг
const rows = computed(() => stepRows(control.ctl?.day?.steps, launching.value))
const progress = computed(() => regionProgress(rows.value, BRAIN_REGIONS.length))
const lit = computed(() => (launching.value ? litNodes(progress.value, BRAIN_REGIONS) : []))
const doneCount = computed(() => rows.value.filter((r) => r.state === 'ok').length)
const stepSince = ref(0)                    // когда начался текущий шаг (по числу готовых шагов)
watch([doneCount, launching], () => { stepSince.value = now.value }, { immediate: true })
const fill = computed(() => (launching.value ? launchFill(rows.value, now.value - stepSince.value) : rows.value.length ? doneCount.value / rows.value.length : 0))
const failed = computed(() => rows.value.some((r) => r.state === 'bad'))
watch(doneCount, (n, o) => { if (n > o) icon.value?.fire() })
watch(failed, (v) => { if (v) details.value = true })

const iconState = computed<BrainIconState>(() => {
  if (control.phase === 'checking' || closing.value) return 'thinking'
  if (finished.value || running.value || launching.value) return 'thinking'
  return 'idle'
})
watch(finished, (v) => { if (v) icon.value?.fire() })

const ctlStep = computed(() => {
  const s = control.ctl?.step
  if (!s || s.name === 'day' || s.name === 'stop') return null
  return CTL_STEP_LABEL[s.name] ?? s.name
})
const launchedAt = ref(0)
watch(launching, (v) => { if (v) launchedAt.value = now.value }, { immediate: true })
const elapsed = computed(() => Math.max(0, Math.round(now.value - launchedAt.value)))
const current = computed(() => rows.value.find((r) => r.state === 'run'))
const stepNo = computed(() => Math.min(rows.value.length, doneCount.value + 1))

// ---- проверки и запуск
const ch = computed(() => control.challenge)
const warns = computed(() => warnCount(ch.value))
const unavailable = computed(() => unavailableReason(action.hello, 'day.start'))
const isLive = computed(() => pick.value === 'live')
const ready = computed(() => control.phase === 'ready' && (isLive.value ? live.value.ok && canBeginLive(ch.value) : canBegin(ch.value)) && !unavailable.value && !action.active)
/** Реальные деньги: проверки допуска словами, пределы из ответа платформы. */
const liveRows = computed(() => (isLive.value ? liveGateRows(ch.value) : []))
const liveBad = computed(() => liveGatesBad(liveRows.value))
const liveLimitLines = computed(() => liveLimits(ch.value))
/** Подтверждение рисков делает общее окно: здесь только запуск действия. Для реальных денег у окна свой заголовок, фразы и nonce подставляет движок из ответа платформы. */
function begin(): void {
  if (!ready.value) return
  const mode = pick.value
  action.run({
    action: 'day.start', args: dayArgs(mode), title: mode === 'live' ? LIVE_CONFIRM_TITLE : `Старт торгового дня: ${modeName(mode)}`, what: mode === 'live' ? LIVE_WHAT : dayWhat(mode),
    timeoutMs: 180_000,                // запуск идёт до минуты и больше: дольше обычных 60 секунд
    onDone: () => { startedByUs.value = true },
  })
}
const gates = computed(() => ch.value?.gates ?? [])
const gateRows = computed(() => gates.value.map((g) => {
  const h = humanize({ kind: 'gate', id: g.id, name: g.name, ok: !!g.ok, msg: g.msg }, { mode: pick.value })
  const own = /не умеем объяснить/.test(h.title) ? plainify([g.name, g.msg].filter(Boolean).join(': ')) : null
  return { id: g.id, ok: !!g.ok, title: own ?? h.title, todo: g.ok ? '' : own ? '' : h.todo }
}))
const failText = (code: string | null) => (code ? failureText(code) : 'Шаг не прошёл проверку. Устраните причину и запустите день снова.')
const gatesOk = computed(() => gates.value.filter((g) => g.ok).length)
const gatesBad = computed(() => gates.value.length - gatesOk.value)
watch(gatesBad, (v) => { if (v && !isLive.value) details.value = true })

// ---- итог
const risk = computed(() => control.ctl?.risk)
const dayId = computed(() => control.ctl?.platform.day_id ?? control.ctl?.day?.day_id ?? null)
const tradeText = computed(() => (platform.value?.trading ? 'торговля включена' : platform.value?.observe ? 'только наблюдение' : 'торговля выключена'))
const facts = computed(() => [modeName(platform.value?.mode ?? control.mode), tradeText.value, `уровень риска ${risk.value?.level ?? '–'}${risk.value?.name ? ' · ' + risk.value.name : ''}`])

let autoTimer: ReturnType<typeof setTimeout> | undefined
const holding = ref(false)
watch(closing, (v) => { if (v) startedByUs.value = false })        // день завершается: прежний «День запущен» уже неверен
watch(finished, (v) => {
  clearTimeout(autoTimer)
  if (v) autoTimer = setTimeout(() => { if (!holding.value) emit('close') }, 6000)
})

// ---- заголовок и подзаголовок
const title = computed(() => ({ pick: isLive.value ? LIVE_TITLE : 'Старт торговли', launch: 'Запускаю день', running: 'Торговый день идёт', closing: 'Завершаю день', done: 'День запущен' })[view.value])
const sub = computed(() => {
  if (view.value === 'launch') return `${ctlStep.value ?? current.value?.label ?? 'Ожидаю сервис управления'} · шаг ${stepNo.value} из ${rows.value.length || 9} · ${elapsed.value} с`
  if (view.value === 'closing') return 'Ждущие входы, чек-лист переноса, аудитор, отчёт. Обычно 10 до 40 секунд.'
  if (view.value === 'pick') return isLive.value ? '' : HINT[pick.value]
  return facts.value.join(' · ')
})

// ---- закрытие и клавиатура
function requestClose(): void {
  if (launching.value) {
    confirmClose.value = true
    return
  }
  emit('close')
}
function onKey(ev: KeyboardEvent): void {
  if (!ownProgress(action.spec?.action, action.phase) && dialogUp(action.phase, action.riskyRun)) return        // поверх открыто общее окно «Принимаю риски»: Escape и Tab принадлежат ему
  if (ev.key === 'Escape') {
    ev.stopPropagation()
    if (confirmClose.value) confirmClose.value = false
    else requestClose()
    return
  }
  if (ev.key !== 'Tab' || !dialog.value) return
  const els = [...dialog.value.querySelectorAll<HTMLElement>('button:not([disabled]), a[href], input:not([disabled]), [tabindex="0"]')].filter((e) => e.offsetParent !== null)
  if (!els.length) return
  const first = els[0]
  const last = els[els.length - 1]
  const act = document.activeElement
  if (ev.shiftKey && (act === first || act === dialog.value)) { ev.preventDefault(); last.focus() }
  else if (!ev.shiftKey && act === last) { ev.preventDefault(); first.focus() }
}
/** Остановка дня: безрисковое или рискованное определяет сервер (challenge); рискованное идёт в общий диалог «Принимаю риски», ход закрытия показывает это окно. */
function stopNow(): void {
  if (action.active) return
  action.run({ action: 'day.stop', args: stopArgs(), title: 'Остановить торговый день', what: STOP_WHAT, timeoutMs: 120_000 })
}
</script>

<template>
  <div class="backdrop" @pointerdown.self="requestClose">
    <div ref="dialog" class="modal" role="dialog" aria-modal="true" aria-labelledby="sd-title" tabindex="-1" @pointerenter="holding = true" @pointerleave="holding = false" @focusin="holding = true">
      <button type="button" class="x" aria-label="Закрыть" @click="requestClose">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>
      </button>

      <div class="stage"><UiBrainIcon ref="icon" :state="iconState" :size="148" crisp :lit="lit" label="Мозг платформы" /></div>

      <h2 id="sd-title" class="title">{{ title }}</h2>
      <p v-if="sub" class="sub" role="status">{{ sub }}</p>

      <!-- выбор режима и проверки -->
      <template v-if="view === 'pick'">
        <UiSegment class="modes" :model-value="pick" :options="PICKS" label="Режим дня" data-autofocus @update:model-value="onPick($event as StartMode)" />

        <!-- реальные деньги: недоступно (серая причина) или усиленный текст, проверки допуска словами, пределы платформы -->
        <Transition name="lv">
          <div v-if="isLive" class="livebox">
            <p v-if="!live.ok" class="note locked" role="note">{{ live.reason }}</p>
            <template v-else>
              <p class="note live" role="note">{{ LIVE_INTRO }}</p>
              <p v-if="control.phase === 'checking' || action.phase === 'checking'" class="note" role="status"><i class="spin" aria-hidden="true" />Проверяю условия запуска…</p>
              <section v-if="liveRows.length" class="lg" aria-labelledby="sd-lg">
                <h3 id="sd-lg" class="lgt">{{ LIVE_GATES_TITLE }}<span :class="liveBad ? 'bad' : 'ok'">{{ liveBad ? `не готово: ${liveBad}` : 'всё готово' }}</span></h3>
                <ul class="lrows" aria-label="Жёсткие проверки допуска">
                  <li v-for="r in liveRows" :key="r.id" :class="r.state">
                    <i class="mark" aria-hidden="true" />
                    <div><span class="t">{{ r.title }}</span><b class="w">{{ r.word }}</b><span v-if="r.todo" class="td">{{ r.todo }}</span></div>
                  </li>
                </ul>
                <p class="fine">{{ LIVE_GATES_NOTE }}</p>
              </section>
              <ul v-if="liveLimitLines.length" class="limits" aria-label="Пределы платформы"><li v-for="l in liveLimitLines" :key="l">{{ l }}</li></ul>
              <p v-if="warns && ready" class="note warn" role="note">{{ warns === 1 ? 'Есть одно предупреждение' : `Есть предупреждений: ${warns}` }}. {{ LIVE_WARN_NOTE }}</p>
            </template>
          </div>
        </Transition>

        <template v-if="!isLive">
          <p v-if="control.phase === 'checking' || action.phase === 'checking'" class="note" role="status"><i class="spin" aria-hidden="true" />Проверяю условия запуска…</p>

          <button v-if="gates.length" type="button" class="more" :aria-expanded="details" aria-controls="sd-gates" @click="details = !details">
            <span class="dot" :class="gatesBad ? 'bad' : 'ok'" aria-hidden="true" />
            {{ gatesBad ? `Не прошло проверок: ${gatesBad}` : `Проверки пройдены: ${gatesOk} из ${gates.length}` }}
            <svg viewBox="0 0 12 12" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 4.5L6 7.5L9 4.5" /></svg>
          </button>
          <div id="sd-gates" class="fold" :class="{ on: details }">
            <ul class="list" aria-label="Проверки готовности">
              <li v-for="g in gateRows" :key="g.id" :class="g.ok ? 'ok' : 'bad'"><i class="mark" aria-hidden="true" /><span>{{ g.title }}</span><em v-if="g.todo">{{ g.todo }}</em></li>
            </ul>
          </div>

          <p v-if="warns && ready" class="note warn" role="note">{{ warns === 1 ? 'Есть одно предупреждение' : `Есть предупреждений: ${warns}` }}. Перед запуском вы увидите последствия и подтвердите их в отдельном окне.</p>
        </template>

        <template v-if="!isLive || live.ok">
          <p v-if="unavailable" class="err" role="alert">{{ unavailable }}</p>
          <p v-if="control.error && (control.phase === 'blocked' || control.phase === 'failed')" class="err" role="alert">{{ control.error }}</p>
        </template>

        <div class="foot">
          <button type="button" class="ghost" @click="requestClose">Отмена</button>
          <button type="button" class="cta" :disabled="!ready" @click="begin">{{ LIVE_START_BUTTON }}</button>
        </div>
        <p class="fine">Уровень риска: {{ risk?.level ?? '–' }}{{ risk?.name ? ' · ' + risk.name : '' }}. Менять его можно в «Система · Настройки». Торговый день идёт до остановки.</p>
      </template>

      <!-- ход запуска -->
      <template v-else-if="view === 'launch'">
        <UiMeter :fill="fill" level="ok" label="Ход запуска дня" class="bar" />
        <button type="button" class="more" :aria-expanded="details" aria-controls="sd-steps" @click="details = !details">
          Подробнее
          <svg viewBox="0 0 12 12" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 4.5L6 7.5L9 4.5" /></svg>
        </button>
        <div id="sd-steps" class="fold" :class="{ on: details }">
          <ol class="list" aria-label="Шаги запуска дня">
            <li v-for="r in rows" :key="r.name" :class="r.state"><i class="mark" aria-hidden="true" /><span>{{ r.label }}</span><em v-if="r.state === 'bad'">{{ failText(r.code) }}</em></li>
          </ol>
        </div>
        <p class="fine">Обычно 20 до 60 секунд. Окно можно закрыть: запуск продолжится.</p>
        <div v-if="confirmClose" class="foot confirm">
          <span>Закрыть окно? Запуск не остановится.</span>
          <button type="button" class="ghost" @click="confirmClose = false">Остаться</button>
          <button type="button" class="cta small" @click="emit('close')">Закрыть</button>
        </div>
      </template>

      <!-- итог после запуска -->
      <template v-else-if="view === 'done'">
        <p v-if="dayId" class="fine">Торговый день: {{ dayLabel(dayId) }}</p>
        <div class="foot"><button type="button" class="cta" data-autofocus @click="emit('close')">Готово</button></div>
      </template>

      <!-- день идёт -->
      <template v-else-if="view === 'running'">
        <p v-if="dayId" class="fine">Торговый день: {{ dayLabel(dayId) }}</p>
        <p v-if="stopCheck" class="note" role="status"><i class="spin" aria-hidden="true" />Проверяю условия остановки…</p>
        <div class="foot">
          <button type="button" class="ghost" data-autofocus @click="requestClose">Закрыть</button>
          <button type="button" class="danger" :disabled="action.active" @click="stopNow">Остановить день</button>
        </div>
        <p class="fine">{{ STOP_WHAT }}</p>
      </template>

      <!-- день завершается -->
      <template v-else>
        <div class="foot"><button type="button" class="ghost" data-autofocus @click="emit('close')">Закрыть окно</button></div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.backdrop { position: fixed; inset: 0; z-index: 60; display: grid; place-items: center; padding: var(--space-4); background: rgba(5, 9, 14, 0.66); }
.modal { position: relative; width: min(420px, 100%); max-height: calc(100vh - 2 * var(--space-4)); overflow-y: auto; display: grid; justify-items: stretch; gap: var(--space-3); padding: var(--space-6) var(--space-5) var(--space-5); background: var(--panel); border: 1px solid var(--line2); border-radius: 20px; box-shadow: 0 24px 64px rgba(0, 0, 0, 0.5); outline: none; text-align: center; }
.x { position: absolute; top: 12px; right: 12px; display: grid; place-items: center; width: 32px; height: 32px; padding: 0; background: transparent; border: 0; border-radius: var(--radius-sm); color: var(--muted); cursor: pointer; transition: color var(--dur-fast) ease; }
.stage { display: grid; place-items: center; height: 156px; margin-bottom: var(--space-1); }
.title { margin: 0; font-size: 20px; font-weight: 700; letter-spacing: -0.02em; }
.sub { margin: -4px 0 0; font-size: 13px; line-height: 1.45; color: var(--muted); font-variant-numeric: tabular-nums; }
.modes { justify-self: center; margin-top: var(--space-2); }
.note { margin: 0; font-size: 13px; line-height: 1.45; display: flex; align-items: center; justify-content: center; gap: var(--space-2); }
.note.warn { color: var(--warn); text-align: left; }
.fine { margin: 0; font-size: 11px; line-height: 1.4; color: var(--muted); }
.spin { flex: none; width: 12px; height: 12px; border-radius: 50%; border: 2px solid var(--line2); border-top-color: var(--accent); animation: sd-spin 0.9s linear infinite; }
@keyframes sd-spin { to { transform: rotate(360deg); } }
.bar { margin: var(--space-1) var(--space-4) 0; }
.bar :deep(.fill) { transition-duration: 1000ms; transition-timing-function: linear; }        /* значение приходит раз в секунду: линейный переход той же длины даёт ровный ход без рывков */

/* «Подробнее»: одна строка-сводка, список раскрывается плавно (строки сетки 0fr -> 1fr) */
.more { justify-self: center; display: inline-flex; align-items: center; gap: var(--space-2); padding: 4px 10px; background: transparent; border: 0; border-radius: 99px; color: var(--muted); font: inherit; font-size: 12px; cursor: pointer; transition: color var(--dur-fast) ease, background-color var(--dur-fast) ease; }
.more svg { transition: transform var(--dur-enter) var(--ease-out); }
.more[aria-expanded='true'] svg { transform: rotate(180deg); }
.more .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--pos); }
.more .dot.bad { background: var(--neg); }
.fold { display: grid; grid-template-rows: 0fr; transition: grid-template-rows var(--dur-enter) var(--ease-out); }
.fold.on { grid-template-rows: 1fr; }
.fold > .list { overflow: hidden; min-height: 0; }
.list { list-style: none; margin: 0; padding: 0; display: grid; gap: 4px; text-align: left; font-size: 12px; }
.fold.on > .list { padding-top: var(--space-1); }
.list li { display: grid; grid-template-columns: 10px minmax(0, 1fr) auto; align-items: baseline; gap: var(--space-2); color: var(--muted); }
.list li.ok, .list li.run { color: var(--text); }
.list li.bad { color: var(--neg); }
.list em { font-style: normal; font-size: 11px; color: var(--muted); }
.mark { width: 7px; height: 7px; border-radius: 50%; align-self: center; background: var(--line2); }
.list li.ok .mark { background: var(--pos); }
.list li.run .mark { background: var(--accent); animation: sd-pulse 1.2s var(--ease-in-out) infinite; }
.list li.bad .mark { background: var(--neg); }
@keyframes sd-pulse { 50% { opacity: 0.35; } }

/* реальные деньги: усиленный текст, проверки допуска со словом статуса, причина недоступности серым */
.livebox { display: grid; gap: var(--space-3); text-align: left; }
.note.live { display: block; padding: var(--space-3); border: 1px solid color-mix(in srgb, var(--warn) 55%, transparent); border-radius: var(--radius-sm); color: var(--text); background: color-mix(in srgb, var(--warn) 7%, transparent); }
.note.locked { display: block; padding: var(--space-3); border: 1px dashed var(--line2); border-radius: var(--radius-sm); color: var(--muted); }
.lg { display: grid; gap: var(--space-2); }
.lgt { margin: 0; display: flex; align-items: baseline; justify-content: space-between; gap: var(--space-3); font-size: 13px; font-weight: 650; }
.lgt span { font-size: 12px; font-weight: 500; font-variant-numeric: tabular-nums; }
.lgt .ok { color: var(--pos); }
.lgt .bad { color: var(--neg); }
.lrows { list-style: none; margin: 0; padding: 0; display: grid; gap: var(--space-2); font-size: 12px; }
.lrows li { display: grid; grid-template-columns: 7px minmax(0, 1fr); gap: var(--space-2); align-items: start; }
.lrows .mark { margin-top: 5px; }
.lrows li.ok .mark { background: var(--pos); }
.lrows li.bad .mark { background: var(--neg); }
.lrows li.later .mark { background: transparent; box-shadow: inset 0 0 0 1.5px var(--muted); }
.lrows li > div { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 2px var(--space-3); min-width: 0; }
.lrows .t { color: var(--text); overflow-wrap: anywhere; }
.lrows .w { flex: none; font-weight: 600; font-size: 11px; color: var(--pos); }
.lrows li.bad .w { color: var(--neg); }
.lrows li.later .w { color: var(--muted); }
.lrows .td { flex: 1 1 100%; color: var(--muted); font-size: 11px; line-height: 1.4; }
.limits { list-style: none; margin: 0; padding: 0; display: grid; gap: 2px; font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.lv-enter-from { opacity: 0; transform: translateY(6px); }
.lv-enter-active { transition: opacity 200ms var(--ease-out), transform 200ms var(--ease-out); }

.err { margin: 0; padding: var(--space-2) var(--space-3); border: 1px solid var(--neg); border-radius: var(--radius-sm); color: var(--neg); font-size: 12px; text-align: left; }

.foot { display: flex; align-items: center; justify-content: center; flex-wrap: wrap; gap: var(--space-2); margin-top: var(--space-2); }
.foot > .cta { flex: 1 1 auto; }
.foot.confirm span { flex: 1 1 100%; font-size: 12px; }
.danger:disabled { opacity: 0.4; cursor: not-allowed; }
.cta { display: inline-flex; align-items: center; justify-content: center; min-height: 42px; padding: 0 20px; background: var(--accent); color: var(--bg); border: 0; border-radius: 12px; font: inherit; font-size: 14px; font-weight: 650; text-decoration: none; cursor: pointer; transition: filter var(--dur-fast) ease, opacity var(--dur-fast) ease, transform 120ms var(--ease-out); }
.cta.small { min-height: 34px; padding: 0 14px; font-size: 13px; }
.cta:active:not(:disabled) { transform: scale(0.98); }
.cta:disabled { opacity: 0.4; cursor: not-allowed; }
.ghost, .danger { min-height: 42px; padding: 0 16px; background: transparent; color: var(--muted); border: 1px solid var(--line2); border-radius: 12px; font: inherit; font-size: 13px; cursor: pointer; transition: color var(--dur-fast) ease, border-color var(--dur-fast) ease, background-color var(--dur-fast) ease; }
.danger { color: var(--neg); border-color: color-mix(in srgb, var(--neg) 50%, transparent); }
.danger.solid { background: var(--neg); color: var(--bg); border-color: var(--neg); font-weight: 650; }
@media (hover: hover) and (pointer: fine) {
  .x:hover, .more:hover { color: var(--text); }
  .ghost:hover { color: var(--text); border-color: var(--muted); }
  .danger:not(.solid):hover { background: color-mix(in srgb, var(--neg) 10%, transparent); }
  .cta:not(:disabled):hover { filter: brightness(1.08); }
}
.cta:focus-visible, .ghost:focus-visible, .danger:focus-visible, .more:focus-visible, .x:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
@media (prefers-reduced-motion: reduce) {
  .spin { animation: none; border-top-color: var(--accent); border-right-color: var(--accent); }
  .list li.run .mark { animation: none; }
  .fold, .more svg, .cta { transition: none; }
  .lv-enter-from { transform: none; }
}
</style>
