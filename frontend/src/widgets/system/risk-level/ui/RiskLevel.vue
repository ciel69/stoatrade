<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useActionStore } from '@/entities/action'
import { useControlStore } from '@/entities/control'
import { RISK_LEVELS, levelLabel, levelName } from '@/entities/risk'
import { UiChangeBar } from '@/shared/ui'
import { reflectText, useRefocus } from '@/shared/lib'
import { useRiskEdit } from '../model/useRiskEdit'

/**
 * Уровень риска рядом с запуском дня: 1 защитный, 5 агрессивный. Показывает текущий уровень, выбор копится черновиком со строкой «было → станет» и последствиями словами;
 * «Применить уровень» запускает общее действие: повышение откроет единое окно «Принимаю риски», понижение применится сразу. Без расширенного управления (auth.dash2_full) остаётся чтение.
 * Отладка без сервера: ?edit=mock (день не идёт), ?edit=mock-run (день идёт).
 */
const control = useControlStore()
const action = useActionStore()
const dbg = new URLSearchParams(location.search).get('edit')
onMounted(() => {
  control.start()
  if (dbg && dbg.startsWith('mock')) {
    action.mockEdit(dbg === 'mock-calm')
    control.mockPlatform(dbg === 'mock-run')
  }
})

const e = useRiskEdit()
const loaded = computed(() => !!control.ctl)
const sum = computed(() => (e.level.value == null ? 'Уровень риска не определён.' : `Сейчас уровень ${levelLabel(e.level.value, e.serverName.value)}.`))

// фокус не теряется, когда панель изменений исчезает вместе с нажатой кнопкой; окно подтверждения возвращает его само
const blocked = (): boolean => action.riskyRun === true && action.phase !== 'idle'
const levels = ref<HTMLElement | null>(null)
const bar = ref<{ $el: HTMLElement } | null>(null)
const focus = useRefocus({ blocked, target: () => levels.value?.querySelector<HTMLElement>('button.cur'), lostIn: () => bar.value?.$el })
watch(e.change, (v, o) => { if (!v && o) focus.arm() })
function apply(): void { focus.mark(); e.submit() }
</script>

<template>
  <section class="risk" aria-label="Уровень риска">
    <header class="top">
      <h2 class="title">Уровень риска</h2>
      <span class="sum">{{ loaded ? sum : '' }}</span>
    </header>
    <div v-if="loaded" class="panel">
      <p class="note">Насколько смелые сделки разрешены. Влияет на размер сделки, допустимый убыток за день и число позиций. Открытые позиции не меняются.</p>
      <div ref="levels" class="levels" role="group" aria-label="Уровень риска">
        <button v-for="n in RISK_LEVELS" :key="n" type="button" class="lv" :class="{ cur: n === e.level.value, pick: n === e.draft.value }" :aria-pressed="n === e.shown.value"
                :disabled="!e.canRisk.value || e.busy.value" @click="e.pick(n)">
          <b>{{ n }}</b>
          <span>{{ levelName(n, n === e.level.value ? e.serverName.value : null) }}</span>
          <i v-if="n === e.level.value" class="now">сейчас</i>
        </button>
      </div>
      <Transition name="bar">
        <UiChangeBar v-if="e.change.value" ref="bar" :line="e.change.value.line" :effect="e.change.value.effect" apply="Применить уровень" :busy="e.busy.value"
                     :hint="e.change.value.up ? 'Повышение риска платформа попросит подтвердить: откроется окно с последствиями.' : undefined" @apply="apply" @reset="e.reset()" />
      </Transition>
      <p class="status" aria-live="polite">{{ reflectText(e.wait.state.value) }}</p>
      <p v-if="!e.canRisk.value" class="locked">Изменение уровня риска выключено: включите расширенное управление (auth.dash2_full в config/control.json). Пока уровень меняется только в терминале.</p>
    </div>
    <div v-else class="skel" aria-busy="true" />
  </section>
</template>

<style scoped>
.risk { display: grid; gap: var(--space-3); }
.top { display: flex; align-items: baseline; flex-wrap: wrap; gap: var(--space-1) var(--space-3); }
.title { margin: 0; font-size: 16px; font-weight: 700; letter-spacing: -0.01em; }
.sum { font-size: 13px; color: var(--muted); }
.panel { display: grid; gap: var(--space-3); padding: var(--space-4); background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); }
.note { margin: 0; font-size: 12px; line-height: 1.5; color: var(--muted); }
.levels { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: var(--space-2); }
.lv { display: grid; justify-items: center; align-content: start; gap: 2px; min-height: 72px; padding: var(--space-2); font: inherit; color: var(--text); background: var(--panel2); border: 1px solid var(--line); border-radius: var(--radius-sm); cursor: pointer; transition: border-color var(--dur-fast) ease, background-color var(--dur-fast) ease, opacity var(--dur-fast) ease, transform 120ms var(--ease-out); }
.lv b { font-size: 18px; font-variant-numeric: tabular-nums; }
.lv span { font-size: 12px; color: var(--muted); text-align: center; overflow-wrap: anywhere; }
.lv .now { font-style: normal; font-size: 11px; color: var(--muted); }
.lv.cur { border-color: var(--line2); }
.lv.pick { background: var(--accent-bg); border-color: var(--accent); }
.lv.pick span { color: var(--text); }
.lv:disabled { cursor: not-allowed; opacity: 0.6; }
.lv:active:not(:disabled) { transform: scale(0.98); }
.status { margin: 0; font-size: 12px; color: var(--muted); }
.status:empty { display: none; }
.locked { margin: 0; padding: var(--space-3); border-radius: var(--radius-sm); background: var(--panel2); font-size: 12px; color: var(--muted); line-height: 1.5; }
.skel { height: 140px; border-radius: var(--radius); background: var(--panel); }
.lv:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
@media (hover: hover) and (pointer: fine) {
  .lv:hover:not(:disabled):not(.pick) { border-color: var(--muted); }
}
.bar-enter-from { opacity: 0; transform: translateY(6px); }
.bar-enter-active { transition: opacity var(--dur-enter) var(--ease-out), transform var(--dur-enter) var(--ease-out); }
.bar-leave-to { opacity: 0; }
.bar-leave-active { transition: opacity 120ms var(--ease-out); }
@media (max-width: 520px) {
  .levels { gap: 4px; }
  .lv { padding: var(--space-2) 2px; }
  .lv span { font-size: 11px; }
}
@media (prefers-reduced-motion: reduce) {
  .bar-enter-from { transform: none; }
  .lv { transition: border-color var(--dur-fast) ease, background-color var(--dur-fast) ease, opacity var(--dur-fast) ease; }
  .lv:active:not(:disabled) { transform: none; }
}
</style>
