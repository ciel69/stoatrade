<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useActionStore } from '@/entities/action'
import { useControlStore } from '@/entities/control'
import { UiChangeBar, UiChip } from '@/shared/ui'
import { modeName, modeOn, reflectText, useRefocus } from '@/shared/lib'
import { useModeEdit } from '../model/useModeEdit'

/**
 * Блок «Режим платформы»: на что идут сделки (бумага, тестовая биржа, наблюдение, живые деньги). Режим меняется только на более безопасный и только пока идёт день.
 * Выбор копится черновиком со строкой «было → станет» и последствиями словами; «Применить» запускает общее действие (единое окно «Принимаю риски», если сервер потребует).
 * Без расширенного управления (auth.dash2_full) остаётся чтение и заметка. Уровень риска меняется не здесь, а на «Система · Управление» рядом с запуском дня.
 * Отладка без сервера: ?edit=mock (платформа остановлена), ?edit=mock-run (день идёт, режим «тестовая биржа»).
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

const e = useModeEdit()
const sum = computed(() => `Платформа торгует ${modeOn(e.mode.value)}.`)
const loaded = computed(() => !!control.ctl)

// фокус не теряется, когда панель изменений исчезает вместе с нажатой кнопкой; окно подтверждения возвращает его само
const blocked = (): boolean => action.riskyRun === true && action.phase !== 'idle'
const modes = ref<HTMLElement | null>(null)
const modeBar = ref<{ $el: HTMLElement } | null>(null)
const modeFocus = useRefocus({ blocked, target: () => modes.value?.querySelector<HTMLElement>('button'), lostIn: () => modeBar.value?.$el })
watch(e.modeChange, (v, o) => { if (!v && o) modeFocus.arm() })
function applyMode(): void { modeFocus.mark(); e.submitMode() }
</script>

<template>
  <section class="board" aria-label="Режим платформы">
    <header class="top">
      <h2 class="title">Режим платформы</h2>
    </header>

    <template v-if="loaded">
      <div class="sum"><p class="line">{{ sum }}</p></div>
      <section class="group">
        <h3 class="cap">На что идут сделки</h3>
        <div class="panel">
          <dl class="kv"><div><dt>Сейчас</dt><dd><UiChip :tone="e.mode.value === 'live' ? 'bad' : 'neutral'">{{ modeName(e.mode.value) }}</UiChip></dd></div></dl>
          <template v-if="e.canMode.value">
            <p v-if="e.modeWhy.value" class="note">{{ e.modeWhy.value }}</p>
            <template v-else>
              <p class="note">Можно перейти только на более безопасный режим. Вернуться на более рискованный отсюда нельзя.</p>
              <div ref="modes" class="opts" role="group" aria-label="Новый режим платформы">
                <button v-for="m in e.safer.value" :key="m" type="button" class="opt" :class="{ pick: m === e.modeDraft.value }" :aria-pressed="m === e.modeDraft.value" :disabled="e.busy.value" @click="e.pickMode(m)">{{ modeName(m) }}</button>
              </div>
              <Transition name="bar">
                <UiChangeBar v-if="e.modeChange.value" ref="modeBar" :line="e.modeChange.value.line" :effect="e.modeChange.value.effect" apply="Сменить режим" :busy="e.busy.value" @apply="applyMode" @reset="e.resetMode()" />
              </Transition>
            </template>
          </template>
          <p v-else class="locked">Изменение режима выключено: включите расширенное управление (auth.dash2_full в config/control.json).</p>
          <p class="status" aria-live="polite">{{ reflectText(e.modeWait.state.value) }}</p>
        </div>
      </section>
    </template>
    <div v-else class="skel" aria-busy="true" />
  </section>
</template>

<style scoped>
.board { display: grid; gap: var(--space-4); }
.top { display: flex; align-items: baseline; gap: var(--space-3); }
.title { margin: 0; font-size: 16px; font-weight: 700; letter-spacing: -0.01em; }
.sum { padding: var(--space-4) var(--space-5); background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); }
.line { margin: 0; font-size: 16px; font-weight: 600; }
.group { display: grid; gap: var(--space-2); }
.cap { margin: 0; font-size: 12px; font-weight: 600; color: var(--muted); }
.panel { display: grid; gap: var(--space-3); padding: var(--space-4); background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); }
.kv { margin: 0; display: grid; gap: 6px; font-size: 13px; }
.kv > div { display: flex; justify-content: space-between; align-items: center; gap: var(--space-4); }
dt { color: var(--muted); }
dd { margin: 0; text-align: right; }
.note { margin: 0; font-size: 12px; line-height: 1.5; color: var(--muted); }
.opts { display: flex; flex-wrap: wrap; gap: var(--space-2); }
.opt { min-height: 40px; padding: 0 var(--space-4); font: inherit; font-size: 13px; color: var(--text); background: var(--panel2); border: 1px solid var(--line); border-radius: var(--radius-sm); cursor: pointer; transition: border-color var(--dur-fast) ease, background-color var(--dur-fast) ease, opacity var(--dur-fast) ease, transform 120ms var(--ease-out); }
.opt.pick { background: var(--accent-bg); border-color: var(--accent); }
.opt:disabled { cursor: not-allowed; opacity: 0.6; }
.opt:active:not(:disabled) { transform: scale(0.98); }
.status { margin: 0; font-size: 12px; color: var(--muted); }
.status:empty { display: none; }
.locked { margin: 0; padding: var(--space-3); border-radius: var(--radius-sm); background: var(--panel2); font-size: 12px; color: var(--muted); line-height: 1.5; }
.skel { height: 160px; border-radius: var(--radius); background: var(--panel); }
.opt:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
@media (hover: hover) and (pointer: fine) {
  .opt:hover:not(:disabled):not(.pick) { border-color: var(--muted); }
}
.bar-enter-from { opacity: 0; transform: translateY(6px); }
.bar-enter-active { transition: opacity var(--dur-enter) var(--ease-out), transform var(--dur-enter) var(--ease-out); }
.bar-leave-to { opacity: 0; }
.bar-leave-active { transition: opacity 120ms var(--ease-out); }
@media (prefers-reduced-motion: reduce) {
  .bar-enter-from { transform: none; }
  .opt { transition: border-color var(--dur-fast) ease, background-color var(--dur-fast) ease, opacity var(--dur-fast) ease; }
  .opt:active:not(:disabled) { transform: none; }
}
</style>
