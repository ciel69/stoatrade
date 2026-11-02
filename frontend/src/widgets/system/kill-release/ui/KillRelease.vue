<script setup lang="ts">
import { onMounted } from 'vue'
import { useActionStore } from '@/entities/action'
import { useControlStore } from '@/entities/control'
import { UiChip } from '@/shared/ui'
import { reflectText } from '@/shared/lib'
import { useKillRelease } from '../model/useKillRelease'

/**
 * Аварийная остановка включена: что случилось (причина, позиции), что будет после снятия и кнопка «Снять аварийную остановку». Кнопка доступна при расширенном управлении
 * и включённом владельцем флаге; иначе здесь инструкция с командой для терминала и кнопкой «Скопировать». Нажатие открывает единое окно «Принимаю риски» с перечнем проверок.
 * Отладка без сервера: ?kill=mock (paper), mock-live (реальные деньги), mock-blocked (позиция без стопа), mock-off (кнопка выключена, только инструкция).
 */
const control = useControlStore()
const action = useActionStore()
const e = useKillRelease()
const dbg = new URLSearchParams(location.search).get('kill')
onMounted(() => {
  control.start()
  if (!dbg || !dbg.startsWith('mock')) return
  const live = dbg === 'mock-live'
  action.mockKill(live ? 'kill-live' : dbg === 'mock-blocked' ? 'kill-blocked' : 'kill')
  control.mockKill(live, dbg === 'mock-off')
  e.mockPositions.value = dbg === 'mock-blocked' ? 1 : 2
})
</script>

<template>
  <Transition name="kr" appear>
    <section v-if="e.shown.value" class="kr" aria-labelledby="kr-title">
      <header class="top">
        <i class="dot" :class="{ ok: !e.active.value }" aria-hidden="true" />
        <h2 id="kr-title" class="title">{{ e.active.value ? 'Аварийная остановка включена' : 'Аварийная остановка снята' }}</h2>
        <UiChip v-if="e.active.value && e.live.value" tone="bad">реальные деньги</UiChip>
      </header>
      <div class="panel">
        <template v-if="e.active.value">
          <p class="lead">Торговля остановлена: новые сделки не открываются, пока остановку не снимут. {{ e.reason.value }}</p>
          <p class="line">{{ e.positionsText.value }}</p>
          <p v-if="e.live.value" class="line warn">Платформа работает на реальные деньги. Снимайте остановку, только если поняли, почему она включилась.</p>

          <div v-if="e.canClick.value" class="act">
            <button type="button" class="btn" :disabled="e.busy.value" @click="e.release()">Снять аварийную остановку</button>
            <div class="checks">
              <p class="hint">Перед снятием платформа проверит:</p>
              <ul>
                <li v-for="c in e.checks.value" :key="c">{{ c }}</li>
              </ul>
              <p class="hint">Если что-то не так, остановка останется, а окно подтверждения объяснит, что именно.</p>
            </div>
          </div>

          <div v-else class="cli">
            <p class="line">{{ e.note.value }}</p>
            <div class="cmd">
              <code>{{ e.command }}</code>
              <button type="button" class="copy" @click="e.copy()">Скопировать команду</button>
            </div>
            <p class="hint">Команда снимает остановку без проверок. Перед ней убедитесь сами, что у каждой открытой позиции есть защитный стоп на бирже.</p>
            <p class="hint" role="status" aria-live="polite">{{ e.copied.value === 'ok' ? 'Команда скопирована.' : e.copied.value === 'fail' ? 'Не удалось скопировать: выделите команду и скопируйте сами.' : '' }}</p>
          </div>
        </template>
        <p v-else class="lead">Платформа остановлена, торговля не идёт. Чтобы торговать, запустите торговый день.</p>
        <p class="status" aria-live="polite">{{ reflectText(e.wait.state.value) }}</p>
      </div>
    </section>
  </Transition>
</template>

<style scoped>
.kr { display: grid; gap: var(--space-3); }
.top { display: flex; align-items: center; flex-wrap: wrap; gap: var(--space-2) var(--space-3); }
.dot { flex: none; width: 10px; height: 10px; border-radius: 50%; background: var(--neg); }
.dot.ok { background: var(--pos); }
.title { margin: 0; font-size: 16px; font-weight: 700; letter-spacing: -0.01em; }
.panel { display: grid; gap: var(--space-3); padding: var(--space-4); background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); }
.panel p { margin: 0; text-wrap: pretty; }
.lead { font-size: 14px; line-height: 1.5; }
.line { font-size: 13px; line-height: 1.5; color: var(--muted); }
.line.warn { color: var(--text); }
.act { display: grid; gap: var(--space-3); justify-items: start; }
.btn { min-height: 40px; padding: 0 var(--space-4); font: inherit; font-size: 14px; font-weight: 600; color: var(--text); background: var(--panel2); border: 1px solid color-mix(in srgb, var(--neg) 55%, var(--line2)); border-radius: var(--radius-sm); cursor: pointer; transition: border-color var(--dur-fast) ease, background-color var(--dur-fast) ease, opacity var(--dur-fast) ease, transform 120ms var(--ease-out); }
.btn:active:not(:disabled) { transform: scale(0.98); }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.checks { display: grid; gap: var(--space-1); }
.checks ul { margin: 0; padding-left: 18px; display: grid; gap: 2px; font-size: 13px; line-height: 1.45; color: var(--muted); }
.hint { font-size: 12px; line-height: 1.5; color: var(--muted); }
.hint:empty { display: none; }
.cli { display: grid; gap: var(--space-2); }
.cmd { display: flex; align-items: center; flex-wrap: wrap; gap: var(--space-2) var(--space-3); }
.cmd code { padding: 6px 10px; background: var(--panel2); border: 1px solid var(--line); border-radius: var(--radius-sm); font-family: var(--mono, ui-monospace, monospace); font-size: 13px; user-select: all; overflow-wrap: anywhere; }
.copy { min-height: 36px; padding: 0 var(--space-3); font: inherit; font-size: 13px; color: var(--text); background: transparent; border: 1px solid var(--line2); border-radius: var(--radius-sm); cursor: pointer; transition: border-color var(--dur-fast) ease, transform 120ms var(--ease-out); }
.copy:active { transform: scale(0.98); }
.status { font-size: 12px; color: var(--muted); }
.status:empty { display: none; }
.btn:focus-visible, .copy:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
@media (hover: hover) and (pointer: fine) {
  .btn:hover:not(:disabled) { border-color: var(--neg); }
  .copy:hover { border-color: var(--muted); }
}
.kr-enter-from { opacity: 0; transform: translateY(6px); }
.kr-enter-active { transition: opacity var(--dur-enter) var(--ease-out), transform var(--dur-enter) var(--ease-out); }
.kr-leave-to { opacity: 0; }
.kr-leave-active { transition: opacity 120ms var(--ease-out); }
@media (max-width: 520px) {
  .panel { padding: var(--space-3); }
  .btn { width: 100%; }
}
@media (prefers-reduced-motion: reduce) {
  .kr-enter-from { transform: none; }
  .btn:active:not(:disabled), .copy:active { transform: none; }
}
</style>
