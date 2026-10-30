<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { CTL_STEP_LABEL, REGION_LABELS, acceptList, canStart, litNodes, regionProgress, stepRows, useControlStore, type DayMode } from '@/entities/control'
import { BRAIN_REGIONS, UiBrainIcon, UiSegment, type BrainIconState } from '@/shared/ui'
import { useNow } from '@/shared/lib'

/**
 * Модалка старта торгового дня. Мозг слева «готовится вместе с платформой»: области коры (нейроны) зажигаются по мере того, как ctl проходит реальные шаги запуска
 * (S1-S9 из состояния дня), а не по таймеру; на каждом новом шаге по сети идёт волна, в конце мозг оживает. Здесь только paper, testnet и observe;
 * live запускается в старом дашборде (токен и фразы). Размер, лимиты и уровень риска только показываются.
 */
const emit = defineEmits<{ close: [] }>()
const control = useControlStore()
const now = useNow()
const OLD_DASH = 'http://127.0.0.1:8766/#control'

const PICKS: ReadonlyArray<{ value: DayMode | 'live'; label: string }> = [
  { value: 'paper', label: 'Бумага' }, { value: 'testnet', label: 'Тестнет' }, { value: 'observe', label: 'Наблюдение' }, { value: 'live', label: 'Live' },
]
const HINT: Record<string, string> = {
  paper: 'Виртуальные сделки без реальных денег.',
  testnet: 'Настоящая биржа, но не настоящие деньги.',
  observe: 'Ничего не торгуется, платформа только наблюдает.',
}

const pick = ref<DayMode | 'live'>(control.mode)
const dialog = ref<HTMLElement | null>(null)
const icon = ref<InstanceType<typeof UiBrainIcon> | null>(null)
const confirmClose = ref(false)
const confirmStop = ref(false)
let opener: HTMLElement | null = null

const platform = computed(() => control.ctl?.platform ?? null)
const running = computed(() => platform.value?.state === 'running')
const launching = computed(() => control.phase === 'starting' || platform.value?.state === 'starting')
const closing = computed(() => control.phase === 'stopping' || platform.value?.state === 'closing')
const finished = computed(() => control.phase === 'done' && control.lastAction === 'day.start')
const view = computed<'launch' | 'running' | 'closing' | 'pick'>(() => (launching.value ? 'launch' : closing.value ? 'closing' : running.value && !finished.value ? 'running' : 'pick'))

onMounted(() => {
  opener = document.activeElement as HTMLElement | null
  document.addEventListener('keydown', onKey, true)
  if (view.value === 'pick' && !finished.value) onPick(pick.value)
  nextTick(() => (dialog.value?.querySelector<HTMLElement>('[data-autofocus] button, button[data-autofocus]') ?? dialog.value)?.focus())
})
onUnmounted(() => {
  document.removeEventListener('keydown', onKey, true)
  clearTimeout(autoTimer)
  opener?.focus?.()
})

function onPick(v: DayMode | 'live'): void {
  pick.value = v
  if (v === 'live') {
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
watch(doneCount, (n, o) => { if (n > o) icon.value?.fire() })

const iconState = computed<BrainIconState>(() => {
  if (control.phase === 'checking' || closing.value) return 'thinking'
  if (finished.value || running.value) return 'thinking'
  return 'idle'
})
watch(finished, (v) => { if (v) icon.value?.fire() })

const ctlStep = computed(() => {
  const s = control.ctl?.step
  if (!s || s.name === 'day' || s.name === 'stop') return null
  return `${CTL_STEP_LABEL[s.name] ?? s.name}${s.n ? ` (${s.i} из ${s.n})` : ''}`
})
const launchedAt = ref(0)
watch(launching, (v) => { if (v) launchedAt.value = now.value }, { immediate: true })
const elapsed = computed(() => Math.max(0, Math.round(now.value - launchedAt.value)))
const current = computed(() => rows.value.find((r) => r.state === 'run'))

// ---- проверки и принятие
const ch = computed(() => control.challenge)
const accept = computed(() => acceptList(ch.value))
const ready = computed(() => control.phase === 'ready' && canStart(ch.value, control.accepted))
const gates = computed(() => ch.value?.gates ?? [])

// ---- итог
const risk = computed(() => control.ctl?.risk)
const dayId = computed(() => control.ctl?.platform.day_id ?? control.ctl?.day?.day_id ?? null)

let autoTimer: ReturnType<typeof setTimeout> | undefined
const holding = ref(false)
watch(finished, (v) => {
  clearTimeout(autoTimer)
  if (v) autoTimer = setTimeout(() => { if (!holding.value) emit('close') }, 6000)
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
function stopNow(): void {
  confirmStop.value = false
  control.stopDay()
}
</script>

<template>
  <div class="backdrop" @pointerdown.self="requestClose">
      <div ref="dialog" class="modal" role="dialog" aria-modal="true" aria-labelledby="sd-title" tabindex="-1" @pointerenter="holding = true" @pointerleave="holding = false" @focusin="holding = true">
        <aside class="brain" aria-hidden="false">
          <UiBrainIcon ref="icon" :state="iconState" :size="136" :lit="lit" label="Мозг платформы" />
          <ul class="regions" aria-label="Области мозга и ход запуска">
            <li v-for="(name, r) in REGION_LABELS" :key="name" :class="{ run: progress[r]?.running && launching, ok: (progress[r]?.done ?? 0) >= 1 && launching }">
              <i class="dot" aria-hidden="true" />{{ name }}
            </li>
          </ul>
        </aside>

        <section class="body">
          <header class="head">
            <h2 id="sd-title" class="title">{{ view === 'running' ? 'Торговый день идёт' : view === 'closing' ? 'Завершение дня' : view === 'launch' ? 'Запускаю день' : finished ? 'День запущен' : 'Старт торговли' }}</h2>
            <button type="button" class="x" aria-label="Закрыть" @click="requestClose">×</button>
          </header>

          <!-- выбор режима и проверки -->
          <template v-if="view === 'pick' && !finished">
            <UiSegment :model-value="pick" :options="PICKS" label="Режим дня" data-autofocus @update:model-value="onPick($event as DayMode | 'live')" />
            <template v-if="pick === 'live'">
              <p class="note warn-note">Реальные деньги запускаются только в старом дашборде: там нужны токен управления, принятие предупреждений и фразы подтверждения.</p>
              <a class="btn primary link" :href="OLD_DASH" target="_blank" rel="noopener">Открыть старый дашборд</a>
            </template>
            <template v-else>
              <p class="note">{{ HINT[pick] }} Торговый день идёт до остановки, горизонт каждой сделки выбирает агент.</p>
              <p class="kv"><span>Уровень риска</span><b>{{ risk?.level ?? '–' }}<template v-if="risk?.name"> · {{ risk.name }}</template></b><span class="muted">меняет только владелец в терминале</span></p>

              <div v-if="control.phase === 'checking'" class="wait" role="status"><i class="spin" aria-hidden="true" />Проверяю условия запуска…</div>
              <ul v-if="gates.length" class="gates" aria-label="Проверки готовности">
                <li v-for="g in gates" :key="g.id" :class="g.ok ? 'ok' : 'bad'">
                  <i class="mark" aria-hidden="true" /><span class="gname">{{ g.id }} {{ g.name }}</span><span v-if="g.msg" class="gmsg">{{ g.msg }}</span>
                </li>
              </ul>
              <div v-if="accept.length" class="accept">
                <p class="note">Предупреждения: отметьте, что принимаете каждое.</p>
                <label v-for="a in accept" :key="a.id" class="check">
                  <input type="checkbox" :checked="control.accepted.has(a.id)" @change="control.toggleAccept(a.id)" />
                  <span>{{ a.msg }}</span>
                </label>
              </div>
              <p v-if="control.error && (control.phase === 'blocked' || control.phase === 'failed')" class="err" role="alert">{{ control.error }}</p>
              <footer class="foot">
                <button type="button" class="btn" @click="requestClose">Отмена</button>
                <button type="button" class="btn primary" :disabled="!ready" @click="control.begin()">Начать</button>
              </footer>
            </template>
          </template>

          <!-- ход запуска -->
          <template v-else-if="view === 'launch'">
            <p class="note" role="status">{{ ctlStep ? `Сейчас: ${ctlStep}` : current ? `Сейчас: ${current.label}` : 'Ожидаю сервис управления…' }} · {{ elapsed }} с</p>
            <ol class="steps" aria-label="Шаги запуска дня">
              <li v-for="r in rows" :key="r.name" :class="r.state">
                <i class="sdot" aria-hidden="true" /><span class="sname">{{ r.name }} {{ r.label }}</span><span v-if="r.code && r.state === 'bad'" class="scode">{{ r.code }}</span>
              </li>
            </ol>
            <p class="note muted">Обычно 20 до 60 секунд. Окно можно закрыть: запуск продолжится.</p>
            <footer v-if="confirmClose" class="foot confirm">
              <span>Закрыть окно? Запуск не остановится.</span>
              <button type="button" class="btn" @click="confirmClose = false">Остаться</button>
              <button type="button" class="btn primary" @click="emit('close')">Закрыть</button>
            </footer>
          </template>

          <!-- итог после запуска -->
          <template v-else-if="finished">
            <dl class="summary">
              <dt>Режим</dt><dd>{{ platform?.mode ?? control.mode }}</dd>
              <dt>День</dt><dd>{{ dayId ?? '–' }}</dd>
              <dt>Сессия</dt><dd>торговый день до остановки</dd>
              <dt>Уровень риска</dt><dd>{{ risk?.level ?? '–' }}<template v-if="risk?.name"> · {{ risk.name }}</template></dd>
              <dt>Торговля</dt><dd>{{ platform?.trading ? 'включена' : platform?.observe ? 'только наблюдение' : 'выключена' }}</dd>
            </dl>
            <footer class="foot"><span class="muted">Окно закроется само</span><button type="button" class="btn primary" data-autofocus @click="emit('close')">Готово</button></footer>
          </template>

          <!-- день идёт -->
          <template v-else-if="view === 'running'">
            <dl class="summary">
              <dt>Режим</dt><dd>{{ platform?.mode ?? '–' }}</dd>
              <dt>День</dt><dd>{{ dayId ?? '–' }}</dd>
              <dt>Торговля</dt><dd>{{ platform?.trading ? 'включена' : platform?.observe ? 'только наблюдение' : 'выключена' }}</dd>
              <dt>Уровень риска</dt><dd>{{ risk?.level ?? '–' }}<template v-if="risk?.name"> · {{ risk.name }}</template></dd>
            </dl>
            <p v-if="control.error && control.phase === 'failed'" class="err" role="alert">{{ control.error }}</p>
            <footer class="foot">
              <template v-if="!confirmStop">
                <button type="button" class="btn" data-autofocus @click="requestClose">Закрыть</button>
                <button type="button" class="btn danger" @click="confirmStop = true">Остановить день</button>
              </template>
              <template v-else>
                <span class="muted">Новые входы прекратятся, день завершится штатно, платформа останется запущенной.</span>
                <button type="button" class="btn" @click="confirmStop = false">Назад</button>
                <button type="button" class="btn danger" @click="stopNow">Остановить</button>
              </template>
            </footer>
          </template>

          <!-- день завершается -->
          <template v-else>
            <p class="note" role="status"><i class="spin" aria-hidden="true" />Завершаю день: ждущие входы, чек-лист переноса, аудитор, отчёт. Обычно 10 до 40 секунд.</p>
            <footer class="foot"><button type="button" class="btn" data-autofocus @click="emit('close')">Закрыть окно</button></footer>
          </template>

        </section>
      </div>
  </div>
</template>

<style scoped>
.backdrop { position: fixed; inset: 0; z-index: 60; display: grid; place-items: center; padding: var(--space-4); background: rgba(5, 9, 14, 0.62); }
.modal { width: min(760px, 100%); max-height: calc(100vh - 2 * var(--space-4)); overflow-y: auto; display: grid; grid-template-columns: 188px minmax(0, 1fr); gap: var(--space-5); padding: var(--space-5); background: var(--panel); border: 1px solid var(--line2); border-radius: var(--radius); box-shadow: 0 16px 48px rgba(0, 0, 0, 0.45); outline: none; }
@media (max-width: 640px) {
  .modal { grid-template-columns: minmax(0, 1fr); }
  .brain { flex-direction: row; align-items: center; justify-content: flex-start; }
}
.brain { display: flex; flex-direction: column; align-items: center; gap: var(--space-4); }
.regions { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; font-size: 12px; color: var(--muted); width: 100%; }
.regions li { display: flex; align-items: center; gap: var(--space-2); transition: color var(--dur-fast) ease; }
.regions .dot { width: 6px; height: 6px; border-radius: 50%; background: var(--line2); transition: background-color var(--dur-fast) ease, transform var(--dur-enter) var(--ease-out); }
.regions li.run { color: var(--text); }
.regions li.run .dot { background: var(--accent); }
.regions li.ok { color: var(--text); }
.regions li.ok .dot { background: var(--accent); transform: scale(1.3); }
.body { min-width: 0; display: grid; gap: var(--space-3); align-content: start; }
.head { display: flex; align-items: center; gap: var(--space-3); }
.title { margin: 0; font-size: 18px; font-weight: 700; letter-spacing: -0.01em; }
.x { margin-left: auto; background: transparent; border: 0; color: var(--muted); font-size: 22px; line-height: 1; padding: 0 var(--space-1); cursor: pointer; }
@media (hover: hover) and (pointer: fine) {
  .x:hover { color: var(--text); }
}
.note { margin: 0; font-size: 13px; line-height: 1.45; color: var(--text); display: flex; align-items: center; gap: var(--space-2); }
.note.muted, .muted { color: var(--muted); }
.warn-note { color: var(--warn); }
.kv { margin: 0; display: flex; flex-wrap: wrap; align-items: baseline; gap: var(--space-2); font-size: 13px; }
.kv span:first-child { color: var(--muted); }
.wait { display: flex; align-items: center; gap: var(--space-2); font-size: 13px; color: var(--muted); }
.spin { flex: none; width: 12px; height: 12px; border-radius: 50%; border: 2px solid var(--line2); border-top-color: var(--accent); animation: sd-spin 0.9s linear infinite; }
@keyframes sd-spin { to { transform: rotate(360deg); } }
.gates, .steps { list-style: none; margin: 0; padding: 0; display: grid; gap: 4px; font-size: 12px; }
.gates li { display: grid; grid-template-columns: 12px auto minmax(0, 1fr); align-items: baseline; gap: var(--space-2); }
.gmsg { color: var(--muted); overflow-wrap: anywhere; }
.mark { width: 8px; height: 8px; border-radius: 50%; align-self: center; background: var(--pos); }
.gates li.bad .mark { background: var(--neg); }
.gates li.bad .gname { color: var(--neg); }
.steps li { display: flex; align-items: center; gap: var(--space-2); color: var(--muted); transition: color var(--dur-fast) ease; }
.sdot { flex: none; width: 8px; height: 8px; border-radius: 50%; background: var(--line2); transition: background-color var(--dur-fast) ease; }
.steps li.ok { color: var(--text); }
.steps li.ok .sdot { background: var(--pos); }
.steps li.run { color: var(--text); font-weight: 600; }
.steps li.run .sdot { background: var(--accent); animation: sd-pulse 1.2s var(--ease-in-out) infinite; }
.steps li.bad { color: var(--neg); }
.steps li.bad .sdot { background: var(--neg); }
.scode { margin-left: auto; font-size: 11px; }
@keyframes sd-pulse { 50% { opacity: 0.35; } }
.accept { display: grid; gap: var(--space-2); }
.check { display: flex; align-items: flex-start; gap: var(--space-2); font-size: 12px; line-height: 1.4; cursor: pointer; }
.check input { margin-top: 2px; accent-color: var(--accent); }
.err { margin: 0; padding: var(--space-2) var(--space-3); border: 1px solid var(--neg); border-radius: var(--radius-sm); color: var(--neg); font-size: 12px; }
.summary { margin: 0; display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 6px var(--space-4); font-size: 13px; }
.summary dt { color: var(--muted); }
.summary dd { margin: 0; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.foot { display: flex; align-items: center; justify-content: flex-end; flex-wrap: wrap; gap: var(--space-2); margin-top: var(--space-2); font-size: 12px; }
.foot.confirm span, .foot > span { margin-right: auto; }
.btn { padding: 7px 16px; background: var(--panel2); color: var(--text); border: 1px solid var(--line2); border-radius: var(--radius-sm); font: inherit; font-size: 13px; cursor: pointer; text-decoration: none; transition: border-color var(--dur-fast) ease, background-color var(--dur-fast) ease, opacity var(--dur-fast) ease; }
.btn.primary { background: var(--accent-bg); color: var(--accent); border-color: var(--accent); }
.btn.danger { color: var(--neg); border-color: var(--neg); }
.btn.link { justify-self: start; }
.btn:disabled { opacity: 0.45; cursor: not-allowed; }
@media (hover: hover) and (pointer: fine) {
  .btn:not(:disabled):hover { border-color: var(--muted); }
  .btn.primary:not(:disabled):hover { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 22%, transparent); }
}
.btn:focus-visible, .check input:focus-visible, .x:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
@media (prefers-reduced-motion: reduce) {
  .spin { animation: none; border-top-color: var(--accent); border-right-color: var(--accent); }
  .steps li.run .sdot { animation: none; }
  .regions .dot { transition: none; }
}
</style>
