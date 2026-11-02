<script setup lang="ts">
import { computed } from 'vue'
import { abortView, autoNote, carryViews, checkViews, closeFailedView, stepRows, useDayStore, type StepRow } from '@/entities/day'
import { UiChip } from '@/shared/ui'
import StepList from './StepList.vue'

/**
 * «Ход дня»: что платформа делала при запуске (девять шагов) и делает при закрытии (восемь шагов), человеческими словами: что шаг проверяет или делает, готово ли, замечание или сбой с причиной, длительность.
 * Сверху причины беды (старт отклонён, закрытие не подтверждено), ниже перенесённые позиции и чек-лист закрытия. Благополучный запуск свёрнут в одну строку, развернуть можно. Без кодов платформы.
 */
const store = useDayStore()
const c = computed(() => store.snapshot?.current ?? null)
const start = computed(() => stepRows(c.value, 'start'))
const stop = computed(() => stepRows(c.value, 'stop'))
const abort = computed(() => (c.value?.abort ? abortView(c.value.abort) : null))
const failed = computed(() => (c.value ? closeFailedView(c.value) : null))
const auto = computed(() => (c.value ? autoNote(c.value) : ''))
const carryIn = computed(() => carryViews(c.value?.carry_in ?? [], c.value))
const carry = computed(() => carryViews(c.value?.carry ?? [], c.value))
const check = computed(() => checkViews(c.value?.checklist ?? [], c.value))

const problem = (rows: StepRow[]): boolean => rows.some((r) => r.state === 'fail' || r.state === 'amber' || r.state === 'run')
const startOpen = computed(() => problem(start.value) || c.value?.state === 'starting')
const stopOpen = computed(() => c.value?.state === 'closing' || problem(stop.value))
const checkOpen = computed(() => c.value?.state === 'closing')

function summary(rows: StepRow[]): string {
  const ok = rows.filter((r) => r.state === 'done' || r.state === 'amber').length
  const amber = rows.filter((r) => r.state === 'amber').length
  const fail = rows.filter((r) => r.state === 'fail').length
  return [`готово ${ok} из ${rows.length}`, amber ? `замечаний: ${amber}` : '', fail ? 'есть сбой' : ''].filter(Boolean).join(', ')
}
</script>

<template>
  <section v-if="c" class="course" aria-labelledby="dc-h">
    <h2 id="dc-h" class="h">Ход дня</h2>

    <div v-if="abort" class="alert bad" role="alert">
      <b>{{ abort.title }}.</b> {{ abort.reason }}.
      <ul v-if="abort.todos.length" class="todo"><li v-for="t in abort.todos" :key="t">{{ t }}</li></ul>
    </div>
    <div v-if="failed" class="alert bad" role="alert">
      <b>{{ failed.title }}.</b> Причина: {{ failed.reason }}.
      <template v-if="failed.plans.length"> Не закрыты: {{ failed.plans.join(', ') }}.</template>
      Позиции под защитой стопов на бирже.
    </div>
    <p v-if="auto" class="sub">{{ auto }}</p>

    <p v-if="!start.length && !stop.length" class="sub">Шагов ещё не было.</p>

    <details v-if="start.length" class="grp" :open="startOpen">
      <summary><span class="gt">Запуск дня</span><span class="sum">{{ summary(start) }}</span></summary>
      <StepList :rows="start" />
    </details>

    <details v-if="stop.length" class="grp" :open="stopOpen">
      <summary><span class="gt">Закрытие дня</span><span class="sum">{{ summary(stop) }}</span></summary>
      <StepList :rows="stop" />
    </details>

    <section v-if="carryIn.length" class="blk" aria-labelledby="dc-in">
      <h3 id="dc-in" class="h3">Перенесено со вчера</h3>
      <ul class="list">
        <li v-for="x in carryIn" :key="x.key"><b>{{ x.title }}</b><span class="muted">{{ x.facts }}</span><span v-if="x.why" class="why">{{ x.why }}</span></li>
      </ul>
    </section>

    <section v-if="carry.length" class="blk" aria-labelledby="dc-out">
      <h3 id="dc-out" class="h3">Перенесено на завтра</h3>
      <ul class="list">
        <li v-for="x in carry" :key="x.key"><b>{{ x.title }}</b><span class="muted">{{ x.facts }}</span><span v-if="x.why" class="why">{{ x.why }}</span></li>
      </ul>
    </section>

    <details v-if="check.length" class="grp" :open="checkOpen">
      <summary><span class="gt">Чек-лист закрытия</span><span class="sum">позиций и заявок: {{ check.length }}</span></summary>
      <p class="sub pad">По каждой позиции платформа проверяет, можно ли оставить её на ночь, и записывает решение.</p>
      <ul class="chk">
        <li v-for="r in check" :key="r.key">
          <b>{{ r.title }}</b>
          <UiChip :tone="r.tone === 'ok' ? 'ok' : r.tone === 'warn' ? 'warn' : 'neutral'">{{ r.decision }}</UiChip>
          <span class="why">{{ r.why }}</span>
        </li>
      </ul>
    </details>
  </section>
</template>

<style scoped>
.course { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: var(--space-5); display: grid; gap: var(--space-4); align-content: start; min-width: 0; }
.h { margin: 0; font-size: 16px; font-weight: 700; letter-spacing: -0.01em; }
.h3 { margin: 0 0 var(--space-2); font-size: 14px; font-weight: 600; }
.sub { margin: 0; font-size: 13px; line-height: 1.5; color: var(--muted); text-wrap: pretty; }
.sub.pad { padding: var(--space-1) 0 var(--space-2); }
.alert { padding: var(--space-3) var(--space-4); border: 1px solid color-mix(in srgb, var(--neg) 55%, var(--line)); border-radius: var(--radius-sm); background: color-mix(in srgb, var(--neg) 9%, var(--panel)); font-size: 13px; line-height: 1.55; text-wrap: pretty; }
.alert b { color: var(--neg); }
.todo { margin: var(--space-1) 0 0; padding-left: 1.2em; color: var(--muted); }
.grp { border-top: 1px solid var(--line); }
.grp > summary { display: flex; align-items: baseline; flex-wrap: wrap; gap: var(--space-1) var(--space-3); min-height: 44px; padding: var(--space-2) 0; cursor: pointer; list-style: none; }
.grp > summary::-webkit-details-marker { display: none; }
.grp > summary::before { content: ''; flex: none; align-self: center; width: 7px; height: 7px; border-right: 1.6px solid var(--muted); border-bottom: 1.6px solid var(--muted); transform: rotate(-45deg); transition: transform var(--dur-fast) var(--ease-out); }
.grp[open] > summary::before { transform: rotate(45deg); }
.grp > summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 2px; }
.gt { font-size: 14px; font-weight: 600; }
.sum { font-size: 12px; color: var(--muted); }
.blk { border-top: 1px solid var(--line); padding-top: var(--space-3); }
.list, .chk { list-style: none; margin: 0; padding: 0; display: grid; }
.list li { display: flex; flex-wrap: wrap; align-items: baseline; gap: var(--space-1) var(--space-3); padding: var(--space-2) 0; font-size: 13px; border-top: 1px solid var(--line); }
.list li:first-child, .chk li:first-child { border-top: 0; }
.muted { color: var(--muted); font-size: 12px; }
.why { flex-basis: 100%; color: var(--muted); font-size: 12px; line-height: 1.5; text-wrap: pretty; }
.chk li { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-1) var(--space-3); padding: var(--space-2) 0; font-size: 13px; border-top: 1px solid var(--line); }
.chk .why { flex: 1 1 220px; flex-basis: auto; }
@media (max-width: 520px) {
  .course { padding: var(--space-4); }
}
@media (prefers-reduced-motion: reduce) {
  .grp > summary::before { transition: none; }
}
</style>
