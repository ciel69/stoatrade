<script setup lang="ts">
import { computed, ref } from 'vue'
import { useActionStore } from '@/entities/action'
import { clock, planTitle, questionViews, useDayStore, type QuestionView } from '@/entities/day'
import { countdown, dayLabel, signed, useNow } from '@/shared/lib'

/**
 * «Нужно ваше решение»: вопросы о переносе позиции через сутки («Перенести» или «Закрыть», без ответа позиция закрывается), «Возобновить день» после перезапуска платформы и готовый итог дня
 * («Открыть отчёт», «Прочитал»). Блок виден, только пока есть что решать. Кнопки идут через общий движок действий: безрисковое выполняется сразу, рискованное (перенос в live) откроет общий
 * диалог «Принимаю риски». После ответа строка сразу показывает «Ответ отправлен», пока тема не обновится. Появление: прозрачность и сдвиг 200 мс, при reduce motion только прозрачность.
 */
const emit = defineEmits<{ 'open-report': [id: string] }>()
const store = useDayStore()
const action = useActionStore()
const now = useNow()

const snap = computed(() => store.snapshot)
const c = computed(() => snap.value?.current ?? null)
const needs = computed(() => store.needs)
const qs = computed<QuestionView[]>(() => questionViews(c.value, needs.value.questions))
const sent = ref<string[]>([])
const busy = computed(() => action.active)
const canAnswer = computed(() => action.can('carry.decide'))
const canResume = computed(() => action.can('day.resume'))
const canAck = computed(() => action.can('report.ack'))
const left = computed(() => (needs.value.deadline != null ? needs.value.deadline - now.value : null))
const reportBrief = computed(() => {
  const id = needs.value.report
  return id ? (snap.value?.days.find((d) => d.day_id === id) ?? (c.value?.day_id === id ? c.value : null)) : null
})
const nobody = computed(() => (needs.value.questions.length > 0 && !canAnswer.value) || (needs.value.resume && !canResume.value) || (!!needs.value.report && !canAck.value))

function mark(key: string): void {
  if (!sent.value.includes(key)) sent.value = [...sent.value, key]
}

function decide(q: QuestionView, decision: 'carry' | 'close'): void {
  if (busy.value || !canAnswer.value) return
  const t = planTitle(q.plan, c.value)
  action.run({
    action: 'carry.decide', args: { plan: q.plan, decision },
    title: decision === 'carry' ? `Перенести ${t} через сутки` : `Закрыть ${t}`,
    what: decision === 'carry' ? 'Позиция останется открытой под стопом на бирже, завтра платформа продолжит её вести.' : 'Позиция будет закрыта при закрытии дня.',
    onDone: () => mark(`q:${q.plan}`),
  })
}

function resume(): void {
  if (busy.value || !canResume.value) return
  action.run({ action: 'day.resume', args: {}, title: 'Возобновить день', what: 'Торговля снова включится: платформа продолжит открывать новые входы по плану дня.', onDone: () => mark('resume') })
}

function ack(): void {
  const id = needs.value.report
  if (busy.value || !canAck.value || !id) return
  action.run({ action: 'report.ack', args: { day_id: id }, title: 'Итог дня прочитан', what: 'Отметка «итог готов» исчезнет.', onDone: () => mark(`r:${id}`) })
}
</script>

<template>
  <Transition name="rise">
    <section v-if="needs.count" class="needs" aria-labelledby="dn-h">
      <h2 id="dn-h" class="h">Нужно ваше решение</h2>

      <div v-if="needs.resume" class="item">
        <div class="what">
          <b>Платформа перезапущена, торговля выключена</b>
          <p class="sub">День {{ dayLabel(c?.day_id) }} продолжается, но новых входов не будет, пока вы его не возобновите. Открытые позиции под защитой стопов на бирже.</p>
        </div>
        <p v-if="sent.includes('resume')" class="done" role="status">Команда отправлена, ждём обновления данных</p>
        <button v-else type="button" class="btn primary" :disabled="busy || !canResume" @click="resume">Возобновить день</button>
      </div>

      <div v-if="qs.length" class="group">
        <p class="lead">
          Перенос позиции через сутки: без ответа{{ needs.deadline != null ? ` до ${clock(needs.deadline)}` : '' }} позиция будет закрыта.
          <span v-if="left != null && left > 0" class="left">Осталось {{ countdown(left) }}.</span>
          <span v-else-if="left != null" class="left late">Срок вышел.</span>
        </p>
        <ul class="qs">
          <li v-for="q in qs" :key="q.id" class="item q">
            <div class="what">
              <b>{{ q.title }}</b>
              <p class="sub">{{ q.text }}</p>
            </div>
            <p v-if="sent.includes(`q:${q.plan}`)" class="done" role="status">Ответ отправлен, ждём обновления данных</p>
            <div v-else class="btns">
              <button type="button" class="btn" :disabled="busy || !canAnswer" @click="decide(q, 'carry')">Перенести</button>
              <button type="button" class="btn" :disabled="busy || !canAnswer" @click="decide(q, 'close')">Закрыть</button>
            </div>
          </li>
        </ul>
      </div>

      <div v-if="needs.report" class="item">
        <div class="what">
          <b>Итог дня {{ dayLabel(needs.report) }} готов<template v-if="reportBrief?.net != null">: {{ signed(reportBrief.net) }} USDT</template></b>
          <p class="sub">Откройте отчёт и нажмите «Прочитал», когда ознакомитесь: отметка «итог готов» тогда исчезнет.</p>
        </div>
        <p v-if="sent.includes(`r:${needs.report}`)" class="done" role="status">Отмечено, ждём обновления данных</p>
        <div v-else class="btns">
          <button type="button" class="btn" @click="emit('open-report', needs.report as string)">Открыть отчёт</button>
          <button type="button" class="btn primary" :disabled="busy || !canAck" @click="ack">Прочитал</button>
        </div>
      </div>

      <p v-if="nobody" class="note">Отвечать отсюда пока нельзя: владельцу нужно включить расширенное управление дашборда. Ответить можно и в старом дашборде.</p>
    </section>
  </Transition>
</template>

<style scoped>
.needs { background: var(--panel); border: 1px solid color-mix(in srgb, var(--warn) 55%, var(--line)); border-radius: var(--radius); padding: var(--space-5); display: grid; gap: var(--space-4); }
.h { margin: 0; font-size: 16px; font-weight: 700; letter-spacing: -0.01em; }
.group { display: grid; gap: var(--space-3); }
.lead { margin: 0; font-size: 13px; line-height: 1.5; color: var(--muted); }
.left { color: var(--text); font-weight: 600; font-variant-numeric: tabular-nums; }
.left.late { color: var(--neg); }
.qs { list-style: none; margin: 0; padding: 0; display: grid; }
.item { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: var(--space-3) var(--space-5); }
.qs .item { padding: var(--space-3) 0; border-top: 1px solid var(--line); }
.what { flex: 1 1 320px; min-width: 0; }
.what b { font-size: 14px; }
.sub { margin: 2px 0 0; font-size: 13px; line-height: 1.5; color: var(--muted); max-width: 72ch; text-wrap: pretty; }
.btns { display: flex; flex-wrap: wrap; gap: var(--space-2); }
.btn { min-height: 40px; padding: 0 var(--space-4); font: inherit; font-size: 13px; font-weight: 600; color: var(--text); background: transparent; border: 1px solid var(--line2); border-radius: var(--radius-sm); cursor: pointer; white-space: nowrap; transition: color var(--dur-fast) ease, border-color var(--dur-fast) ease, opacity var(--dur-fast) ease, transform 120ms var(--ease-out); }
.btn.primary { background: var(--accent); border-color: transparent; color: var(--bg); }
.btn:disabled { opacity: 0.45; cursor: not-allowed; }
.btn:active:not(:disabled) { transform: scale(0.97); }
.btn:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.done { margin: 0; font-size: 13px; color: var(--muted); }
.note { margin: 0; padding: var(--space-3); border-radius: var(--radius-sm); background: var(--panel2); font-size: 12px; line-height: 1.5; color: var(--muted); }
@media (hover: hover) and (pointer: fine) {
  .btn:hover:not(:disabled):not(.primary) { border-color: var(--muted); }
  .btn.primary:hover:not(:disabled) { filter: brightness(1.08); }
}
@media (max-width: 520px) {
  .needs { padding: var(--space-4); }
  .btns { width: 100%; }
  .btns .btn { flex: 1 1 0; }
}
.rise-enter-active { transition: opacity var(--dur-enter) var(--ease-out), transform var(--dur-enter) var(--ease-out); }
.rise-leave-active { transition: opacity 120ms var(--ease-out); }
.rise-enter-from { opacity: 0; transform: translateY(6px); }
.rise-leave-to { opacity: 0; }
@media (prefers-reduced-motion: reduce) {
  .rise-enter-from { transform: none; }
  .btn { transition: color var(--dur-fast) ease, border-color var(--dur-fast) ease, opacity var(--dur-fast) ease; }
  .btn:active:not(:disabled) { transform: none; }
}
</style>
