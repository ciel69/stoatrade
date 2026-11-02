<script setup lang="ts">
import { computed } from 'vue'
import { verdictView, type LearningVerdict } from '@/entities/learning'
import { LEARN_VERDICT_FACT, LEARN_VERDICT_NO_DIFF, LEARN_VERDICT_REGION, LEARN_VERDICT_SPLIT } from '@/shared/lib'
import { guardRows } from '../lib/verdict'
import DiffRange from './DiffRange.vue'

/**
 * Первый элемент страницы «Учится ли мозг»: одно из трёх слов, пояснение от сервера, число сделок, разность недавних и прежних с интервалом, защиты.
 * Состояние передают слово и значок формы (кружок с пунктиром, знак равенства, галочка), а не один цвет. Предупреждение «стало хуже» отдельной строкой.
 * Только чтение; данные приходят готовыми, правило вердикта считает сервер.
 */
const props = defineProps<{ verdict: LearningVerdict }>()
const view = computed(() => verdictView(props.verdict))
const guards = computed(() => (props.verdict.guards ? guardRows(props.verdict.guards) : []))
const split = computed(() => (props.verdict.n_recent != null && props.verdict.n_prev != null ? LEARN_VERDICT_SPLIT(props.verdict.n_recent, props.verdict.n_prev) : ''))
</script>

<template>
  <section class="vd" :class="`st-${view.state}`" :aria-label="LEARN_VERDICT_REGION">
    <div class="top">
      <svg class="mark" viewBox="0 0 24 24" width="24" height="24" aria-hidden="true" focusable="false">
        <circle v-if="view.state === 'few_data'" class="ring dash" cx="12" cy="12" r="9.5" />
        <circle v-else class="ring" cx="12" cy="12" r="9.5" />
        <path v-if="view.state === 'no_difference'" class="glyph" d="M7.5 9.5h9M7.5 14.5h9" />
        <path v-else-if="view.state === 'confirmed'" class="glyph" d="M7.5 12.5l3 3 6-7" />
        <circle v-else class="fill" cx="12" cy="12" r="2" />
      </svg>
      <h2 class="word">{{ view.word }}</h2>
    </div>
    <p class="text">{{ view.text }}</p>
    <p v-if="view.warn" class="warn">
      <svg class="tri" viewBox="0 0 20 20" width="16" height="16" aria-hidden="true" focusable="false"><path d="M10 3.2L18 16.8H2z" /><path d="M10 8.5v4M10 14.6v.01" /></svg>
      <span>{{ view.warn }}</span>
    </p>
    <dl class="facts">
      <div class="fact">
        <dt>{{ LEARN_VERDICT_FACT.trades }}</dt>
        <dd>{{ verdict.n }}</dd>
      </div>
      <div class="fact wide">
        <dt>{{ LEARN_VERDICT_FACT.diff }}</dt>
        <dd v-if="view.diff" class="diff">
          <DiffRange :diff="verdict.diff_r" />
          <span class="num">{{ view.diff }}</span>
        </dd>
        <dd v-else class="none">{{ LEARN_VERDICT_NO_DIFF }}</dd>
      </div>
      <div v-if="split" class="fact">
        <dt>{{ LEARN_VERDICT_FACT.split }}</dt>
        <dd>{{ split }}</dd>
      </div>
      <div v-if="guards.length" class="fact wide">
        <dt>{{ LEARN_VERDICT_FACT.guards }}</dt>
        <dd>
          <ul class="guards">
            <li v-for="g in guards" :key="g.id" :class="`g-${g.state}`"><b class="gm">{{ g.mark }}</b><span>{{ g.text }}</span></li>
          </ul>
        </dd>
      </div>
    </dl>
  </section>
</template>

<style scoped>
.vd { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: var(--space-5); display: grid; gap: var(--space-3); min-width: 0; transition: opacity var(--dur-enter) var(--ease-out), transform var(--dur-enter) var(--ease-out); }
.top { display: flex; align-items: center; gap: var(--space-3); }
.mark { flex: none; }
.ring { fill: none; stroke: currentColor; stroke-width: 1.8; }
.ring.dash { stroke-dasharray: 3 3.2; }
.glyph { fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
.fill { fill: currentColor; }
.st-few_data .mark { color: var(--muted); }
.st-no_difference .mark { color: var(--text); }
.st-confirmed .mark { color: var(--pos); }
.word { margin: 0; font-size: 22px; font-weight: 700; line-height: 1.3; letter-spacing: -0.01em; text-wrap: balance; }
.text { margin: 0; font-size: 14px; line-height: 1.5; color: var(--muted); max-width: 70ch; text-wrap: pretty; }
.warn { margin: 0; display: flex; align-items: flex-start; gap: var(--space-2); padding: var(--space-2) var(--space-3); border: 1px solid var(--warn); border-radius: var(--radius-sm); background: color-mix(in srgb, var(--warn) 9%, transparent); color: var(--warn); font-size: 13px; font-weight: 600; line-height: 1.45; }
.tri { flex: none; margin-top: 2px; fill: none; stroke: currentColor; stroke-width: 1.6; stroke-linecap: round; stroke-linejoin: round; }
.facts { margin: 0; display: flex; flex-wrap: wrap; gap: var(--space-3) var(--space-6); padding-top: var(--space-3); border-top: 1px solid var(--line); }
.fact { display: grid; gap: 2px; min-width: 0; align-content: start; }
.fact dt { font-size: 12px; color: var(--muted); }
.fact dd { margin: 0; font-size: 14px; font-weight: 600; font-variant-numeric: tabular-nums; text-wrap: pretty; }
.fact dd.none { font-weight: 400; color: var(--muted); }
.diff { display: flex; align-items: center; flex-wrap: wrap; gap: var(--space-2) var(--space-3); }
.num { min-width: 0; }
.guards { margin: 0; padding: 0; list-style: none; display: grid; gap: 2px; }
.guards li { display: flex; align-items: baseline; gap: var(--space-2); font-weight: 400; font-size: 13px; }
.gm { flex: none; min-width: 86px; font-size: 12px; font-weight: 600; }
.g-ok .gm { color: var(--pos); }
.g-bad .gm { color: var(--neg); }
.g-none .gm { color: var(--muted); }

/* появление при заходе и смене режима: прозрачность и сдвиг на 4 px, один раз */
@starting-style {
  .vd { opacity: 0; transform: translateY(4px); }
}
@media (prefers-reduced-motion: reduce) {
  .vd { transition: opacity var(--dur-enter) ease; }
  @starting-style {
    .vd { transform: none; }
  }
}
@media (max-width: 520px) {
  .vd { padding: var(--space-4); }
  .word { font-size: 19px; }
  .facts { gap: var(--space-3); }
  .fact.wide { flex-basis: 100%; }
  .guards li { flex-direction: column; gap: 0; }
}
</style>
