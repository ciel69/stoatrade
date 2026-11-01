<script setup lang="ts">
import { computed } from 'vue'
import { useActionStore } from '@/entities/action'
import { useControlStore, type PlatformState } from '@/entities/control'
import { useSystemStore } from '@/entities/system'
import { UiChip } from '@/shared/ui'
import { dayLabel, humanize, modeName, sessionName, type Headline } from '@/shared/lib'

/**
 * Главное на странице: что с платформой и что делать. Слева состояние словами (работает, остановлена, запускается) с режимом, днём и сессией, под ним вывод по узлам (всё работает
 * или что проверить). Справа одно главное действие: запустить день, остановить день или показать ход; недоступное действие объясняет причину рядом. Параметры, проверки и подтверждения
 * показывает то же окно, что открывает кнопка в боковой колонке. Аварийная остановка и смена режима и риска здесь не делаются: сервер дашборда принимает только безопасный старт и стоп дня.
 */
/** Вывод «можно ли оставить платформу торговать» считает индикатор состояния (он же в шапке); страница передаёт его сюда, чтобы два места не расходились. */
const props = defineProps<{ headline?: Headline | null }>()
const control = useControlStore()
const sys = useSystemStore()
const action = useActionStore()

const STATE: Record<PlatformState, string> = { stopped: 'Платформа остановлена', starting: 'Платформа запускается', running: 'Платформа работает', closing: 'День завершается', halted: 'Платформа остановилась аварийно', unknown: 'Состояние неизвестно' }
const LABEL = { start: 'Запустить день', starting: 'Показать ход запуска', running: 'Остановить день', closing: 'Показать ход завершения', blocked: 'Запуск недоступен', unavailable: 'Управление недоступно' } as const

const p = computed(() => control.ctl?.platform ?? null)
const state = computed<PlatformState>(() => (control.ctl ? (p.value?.state ?? 'unknown') : 'unknown'))
const btn = computed(() => control.button)
const v = computed(() => props.headline ?? null)
/** Цвет точки: тот же, что у индикатора в шапке (зелёный, жёлтый, красный; серый, когда торговли нет). */
const tone = computed(() => (v.value ? (v.value.tone === 'idle' ? 'idle' : v.value.tone === 'ok' ? 'ok' : v.value.tone === 'warn' ? 'warn' : v.value.tone === 'bad' ? 'bad' : 'ok') : 'idle'))
const sub = computed(() => {
  const x = p.value
  if (!x) return 'Нет связи со службой управления.'
  return [x.mode ? modeName(x.mode) : null, x.day_id ? dayLabel(x.day_id) : null, x.session ? sessionName(x.session) : null, state.value === 'running' ? (x.trading ? 'торговля включена' : 'торговля выключена') : null].filter(Boolean).join(' · ')
})
/** Сообщение платформы и предупреждения системы простым языком; исходный текст сервера в подробностях для специалиста. */
const banner = computed(() => (control.ctl?.banner?.text ? humanize({ kind: 'banner', level: control.ctl.banner.level, text: control.ctl.banner.text }, { mode: p.value?.mode, running: state.value === 'running' }) : null))
const warns = computed(() => (sys.snapshot?.warnings ?? []).filter((w) => w.text).map((w) => humanize({ kind: 'text', level: w.level ?? 'info', text: w.text as string })))
const techLines = computed(() => [banner.value?.details, ...warns.value.map((w) => w.details)].filter(Boolean).join('\n'))
const disabled = computed(() => btn.value.kind === 'unavailable' || btn.value.kind === 'blocked')

/** Аварийная остановка: только при расширенном управлении (hello.full и kill.set в modes); если KILL уже активен, кнопки нет, а как снять, сказано в сводке выше. */
const killShown = computed(() => action.can('kill.set') && !sys.snapshot?.kill && !control.ctl?.kill)
/** Без закрытия позиций ctl считает остановку безрисковой: команда уходит сразу, итог придёт уведомлением. С закрытием откроется общий диалог рисков. */
function stopNow(flatten: boolean): void {
  action.run({
    action: 'kill.set',
    args: { flatten, reason: 'из дашборда' },
    title: flatten ? 'Аварийная остановка с закрытием позиций' : 'Аварийная остановка',
    what: flatten ? 'Торговля остановится, открытые позиции будут закрыты по рынку.' : 'Торговля остановится, открытые позиции остаются как есть.',
  })
}
</script>

<template>
  <section class="hero" :class="`lv-${tone}`" aria-label="Состояние платформы">
    <div class="row">
      <i class="dot" aria-hidden="true" />
      <div class="txt">
        <h2 class="title">{{ STATE[state] }}<UiChip v-if="p?.mode === 'live'" tone="bad">реальные деньги</UiChip></h2>
        <p class="sub">{{ sub }}</p>
      </div>
      <div class="act">
        <button type="button" class="cta" :class="{ primary: btn.kind === 'start', stop: btn.kind === 'running' }" :disabled="disabled" @click="control.openDialog()">{{ LABEL[btn.kind] }}</button>
        <p v-if="btn.why" class="why">{{ btn.why }}</p>
        <div v-if="killShown" class="kill">
          <button type="button" class="stopbtn" @click="stopNow(false)">Аварийная остановка</button>
          <button type="button" class="link" @click="stopNow(true)">С закрытием позиций</button>
        </div>
      </div>
    </div>

    <p v-if="v" class="verdict"><b>{{ v.title }}.</b> {{ v.detail }}</p>
    <p v-if="control.ctl && !control.ctl.alive" class="note bad">Служба управления не отвечает, поэтому запуск и остановка дня сейчас недоступны. Возможно, платформа не запущена: запустите её в терминале.</p>
    <div v-if="banner" class="note" :class="banner.tone">
      <p class="n-title">{{ banner.title }}</p>
      <p v-if="banner.what" class="n-text">{{ banner.what }}</p>
      <p v-if="banner.why" class="n-text">{{ banner.why }}</p>
      <p v-if="banner.todo" class="n-text n-todo">{{ banner.todo }}</p>
    </div>
    <ul v-if="warns.length" class="warns">
      <li v-for="(w, i) in warns" :key="i" :class="w.tone">
        <b>{{ w.title }}.</b> <span>{{ [w.what, w.todo].filter(Boolean).join(' ') }}</span>
      </li>
    </ul>
    <details v-if="techLines" class="tech"><summary>Подробности для специалиста</summary><pre>{{ techLines }}</pre></details>
  </section>
</template>

<style scoped>
.hero { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: var(--space-5); display: grid; gap: var(--space-3); }
.row { display: flex; align-items: center; flex-wrap: wrap; gap: var(--space-4); }
.dot { flex: none; width: 12px; height: 12px; border-radius: 50%; background: var(--pos); }
.lv-warn .dot { background: var(--warn); }
.lv-bad .dot { background: var(--neg); }
.lv-idle .dot { background: var(--muted); }
.txt { flex: 1 1 260px; min-width: 0; }
.title { margin: 0; font-size: 20px; font-weight: 700; letter-spacing: -0.01em; display: flex; align-items: center; gap: var(--space-2); }
.sub { margin: 2px 0 0; font-size: 13px; color: var(--muted); }
.act { display: grid; gap: 4px; justify-items: end; margin-left: auto; }
.cta { min-width: 190px; height: 40px; padding: 0 var(--space-4); border-radius: var(--radius-sm); border: 1px solid var(--line2); background: var(--panel2); color: var(--text); font: inherit; font-size: 14px; font-weight: 600; cursor: pointer; transition: background-color var(--dur-fast) ease, border-color var(--dur-fast) ease, transform 120ms var(--ease-out); }
.cta.primary { background: var(--accent); border-color: transparent; color: var(--bg); }
.cta.stop { border-color: color-mix(in srgb, var(--neg) 50%, var(--line2)); }
.cta:active:not(:disabled) { transform: scale(0.97); }
.cta:disabled { opacity: 0.5; cursor: not-allowed; }
.cta:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.why { margin: 0; max-width: 260px; font-size: 12px; color: var(--muted); text-align: right; }
.kill { display: flex; align-items: center; gap: var(--space-3); margin-top: var(--space-1); }
.stopbtn, .link { padding: 4px 0; background: transparent; border: 0; font: inherit; font-size: 12px; cursor: pointer; transition: color var(--dur-fast) ease; }
.stopbtn { color: var(--neg); font-weight: 600; }
.link { color: var(--muted); text-decoration: underline; text-underline-offset: 3px; text-decoration-color: var(--line2); }
.stopbtn:focus-visible, .link:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 2px; }
.verdict { margin: 0; font-size: 13px; color: var(--muted); }
.verdict b { color: var(--text); }
.note { margin: 0; padding: var(--space-3); border-radius: var(--radius-sm); background: var(--panel2); font-size: 13px; line-height: 1.5; display: grid; gap: 2px; }
.note p { margin: 0; text-wrap: pretty; }
.n-title { font-weight: 600; }
.n-text { color: var(--muted); }
.note.bad .n-title { color: var(--neg); }
.note.warn .n-title { color: var(--warn); }
.warns { list-style: none; margin: 0; padding: 0; display: grid; gap: var(--space-2); font-size: 13px; line-height: 1.5; color: var(--muted); }
.warns b { color: var(--text); }
.warns .warn b { color: var(--warn); }
.warns .bad b { color: var(--neg); }
.tech { font-size: 12px; color: var(--muted); }
.tech summary { cursor: pointer; min-height: 32px; display: flex; align-items: center; }
.tech summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 2px; }
.tech pre { margin: var(--space-1) 0 0; padding: var(--space-3); background: var(--panel2); border-radius: var(--radius-sm); font-family: var(--mono, ui-monospace, monospace); font-size: 11px; white-space: pre-wrap; overflow-wrap: anywhere; user-select: all; }
@media (hover: hover) and (pointer: fine) {
  .cta:hover:not(:disabled):not(.primary) { border-color: var(--muted); }
  .cta.primary:hover:not(:disabled) { filter: brightness(1.08); }
  .link:hover { color: var(--text); }
  .stopbtn:hover { text-decoration: underline; text-underline-offset: 3px; }
}
@media (prefers-reduced-motion: reduce) {
  .cta:active:not(:disabled) { transform: none; }
}
</style>
