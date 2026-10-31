<script setup lang="ts">
import { computed } from 'vue'
import { useActionStore } from '@/entities/action'
import { useControlStore, type PlatformState } from '@/entities/control'
import { useSystemStore, verdict, worst } from '@/entities/system'
import { UiChip } from '@/shared/ui'

/**
 * Главное на странице: что с платформой и что делать. Слева состояние словами (работает, остановлена, запускается) с режимом, днём и сессией, под ним вывод по узлам (всё работает
 * или что проверить). Справа одно главное действие: запустить день, остановить день или показать ход; недоступное действие объясняет причину рядом. Параметры, проверки и подтверждения
 * показывает то же окно, что открывает кнопка в боковой колонке. Аварийная остановка и смена режима и риска здесь не делаются: сервер дашборда принимает только безопасный старт и стоп дня.
 */
const control = useControlStore()
const sys = useSystemStore()
const action = useActionStore()

const STATE: Record<PlatformState, string> = { stopped: 'Платформа остановлена', starting: 'Платформа запускается', running: 'Платформа работает', closing: 'День завершается', halted: 'Аварийно остановлена', unknown: 'Состояние неизвестно' }
const MODE: Record<string, string> = { observe: 'Наблюдение', paper: 'Бумага', testnet: 'Тестовая сеть', live: 'Реальные деньги' }
const LABEL = { start: 'Запустить день', starting: 'Показать ход запуска', running: 'Остановить день', closing: 'Показать ход завершения', blocked: 'Запуск недоступен', unavailable: 'Управление недоступно' } as const

const p = computed(() => control.ctl?.platform ?? null)
const state = computed<PlatformState>(() => (control.ctl ? (p.value?.state ?? 'unknown') : 'unknown'))
const btn = computed(() => control.button)
const v = computed(() => (sys.snapshot ? verdict(sys.snapshot) : null))
/** Цвет сводки: худшее из состояния платформы и узлов. */
const tone = computed(() => worst([sys.snapshot?.level, state.value === 'halted' ? 'bad' : state.value === 'unknown' ? 'warn' : state.value === 'starting' || state.value === 'closing' ? 'warn' : 'ok', control.ctl?.alive === false ? 'bad' : 'ok']))
const sub = computed(() => {
  const x = p.value
  if (!x) return 'Нет связи с сервисом управления.'
  return [x.mode ? (MODE[x.mode] ?? x.mode) : null, x.day_id ? `день ${x.day_id}` : null, x.session ? `сессия «${x.session}»` : null, state.value === 'running' ? (x.trading ? 'торговля включена' : 'торговля выключена') : null].filter(Boolean).join(' · ')
})
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
        <h2 class="title">{{ STATE[state] }}<UiChip v-if="p?.mode === 'live'" tone="bad">live</UiChip></h2>
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
    <p v-if="control.ctl && !control.ctl.alive" class="note bad">Сервис управления не отвечает: действия заблокированы. Платформа запускается в терминале: <code>./tb up --detach</code>.</p>
    <p v-if="control.ctl?.banner?.text" class="note" :class="control.ctl.banner.level === 'bad' ? 'bad' : control.ctl.banner.level === 'warn' ? 'warn' : ''">{{ control.ctl.banner.text }}</p>
    <ul v-if="sys.snapshot?.warnings.length" class="warns">
      <li v-for="(w, i) in sys.snapshot.warnings" :key="i" :class="w.level === 'bad' ? 'bad' : 'warn'">{{ w.text }}</li>
    </ul>
  </section>
</template>

<style scoped>
.hero { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: var(--space-5); display: grid; gap: var(--space-3); border-left-width: 3px; }
.hero.lv-ok { border-left-color: var(--pos); }
.hero.lv-warn { border-left-color: var(--warn); }
.hero.lv-bad { border-left-color: var(--neg); }
.row { display: flex; align-items: center; flex-wrap: wrap; gap: var(--space-4); }
.dot { flex: none; width: 12px; height: 12px; border-radius: 50%; background: var(--pos); }
.lv-warn .dot { background: var(--warn); }
.lv-bad .dot { background: var(--neg); }
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
.note { margin: 0; padding: var(--space-3); border-radius: var(--radius-sm); background: var(--panel2); font-size: 12px; line-height: 1.5; }
.note.bad { color: var(--neg); }
.note.warn { color: var(--warn); }
.warns { list-style: none; margin: 0; padding: 0; display: grid; gap: 4px; font-size: 12px; }
.warns .warn { color: var(--warn); }
.warns .bad { color: var(--neg); }
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
