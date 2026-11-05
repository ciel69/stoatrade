<script setup lang="ts">
import { computed } from 'vue'
import { blockView, type ExitsBlock } from '@/entities/learning'
import { LEARN_SMALL } from '@/shared/lib'
import { UiBandChart, UiChip } from '@/shared/ui'
import { CAPTURE_EXPLAIN, MFE_EXPLAIN, exitRows, pctText, pickSeries, r4View, windowText } from '../lib/exits'

/**
 * Блок «Выходы»: сначала R4 — версия политики выхода и её сравнение с X-0001 (разность по путям и сделкам мозга,
 * забранное MFE, издержки, испытания и сторож). Ниже, как справка R0.2, окна доли забранного и ряд по номеру сделки.
 * Только чтение, без переключателей.
 */
const props = defineProps<{ block: ExitsBlock; computingForS?: number }>()

const view = computed(() => blockView(props.block, props.computingForS ?? 0))
const hasData = computed(() => view.value.kind === 'data')
const r4v = computed(() => r4View(props.block.r4))
const rows = computed(() => exitRows(props.block.windows ?? {}))
const chart = computed(() => pickSeries(props.block.series ?? {}))
const anySmall = computed(() => rows.value.some((r) => r.small && r.nPath > 0))
const chartLabel = computed(() => (chart.value ? `Доля забранного, ${windowText(chart.value.key).toLowerCase()} сделок` : 'Доля забранного'))
</script>

<template>
  <section class="blk" aria-labelledby="learn-exits-h" data-block="exits">
    <header class="head">
      <h2 id="learn-exits-h" class="title">{{ block.title }}</h2>
      <UiChip v-if="!r4v.empty" :tone="r4v.tone">{{ r4v.word }}</UiChip>
      <UiChip v-if="hasData && view.few" tone="warn">{{ LEARN_SMALL() }}</UiChip>
    </header>

    <template v-if="!r4v.empty">
      <p v-if="r4v.version" class="ver">{{ r4v.version }}<span v-if="r4v.reason" class="reason"> · {{ r4v.reason }}</span></p>
      <p v-if="r4v.text" class="verdict">{{ r4v.text }}</p>
      <p v-if="r4v.note" class="lead">{{ r4v.note }}</p>

      <div class="mx-wrap" role="region" aria-label="Выходы: сравнение версий" tabindex="0">
        <div class="mx" role="table" aria-label="Сравнение действующей версии выходов с X-0001">
          <div class="mx-head" role="row">
            <span role="columnheader">Показатель</span>
            <span role="columnheader" class="num">Значение</span>
            <span role="columnheader" class="num">95% интервал</span>
            <span role="columnheader" class="num">Основание</span>
          </div>
          <div v-for="(r, i) in r4v.rows" :key="r.key" class="mrow" role="row" :style="{ '--i': i }">
            <div role="rowheader" class="mh">{{ r.label }}</div>
            <div role="cell" class="num cell" data-label="Значение">
              <span v-if="r.pair.length" class="pair">
                <span v-for="(p, j) in r.pair" :key="p.label" class="pv"><span v-if="j" class="psep" aria-hidden="true">· </span><span class="pl">{{ p.label }}</span> <b class="px">{{ p.value }}</b></span>
              </span>
              <span v-else class="mv" :class="r.tone">{{ r.value }}</span>
              <span v-if="r.note" class="mnote">{{ r.note }}</span>
            </div>
            <div role="cell" class="num cell" data-label="95% интервал"><span class="mi">{{ r.interval || '—' }}</span></div>
            <div role="cell" class="num cell" data-label="Основание"><span class="mi">{{ r.counter || '—' }}</span></div>
          </div>
        </div>
      </div>

      <footer class="r4-foot">
        <p class="foot">{{ r4v.trials }}</p>
        <p class="foot">{{ r4v.watch }}<template v-if="r4v.codes"> · {{ r4v.codes }}</template></p>
      </footer>
    </template>
    <p v-else class="state r4-empty">{{ r4v.note }}</p>

    <template v-if="hasData">
      <h3 class="sub">Доля забранного по сделкам</h3>
      <p class="lead">{{ CAPTURE_EXPLAIN }}</p>
      <p v-if="block.note" class="lead">{{ block.note }}</p>

      <div class="scroll" tabindex="0" role="region" aria-label="Выходы по окнам">
        <table class="tbl">
          <caption class="sr">Выходы по окнам</caption>
          <thead>
            <tr><th scope="col">Окно</th><th scope="col" class="num">Забрано</th><th scope="col" class="num">Лучший ход</th><th scope="col" class="num">Сделок с путём</th></tr>
          </thead>
          <tbody>
            <tr v-for="r in rows" :key="r.key">
              <th scope="row" class="wk">{{ r.label }}</th>
              <td class="num"><span class="v big">{{ r.capture.value }}</span><span v-if="r.capture.interval" class="iv">{{ r.capture.interval }}</span></td>
              <td class="num"><span class="v">{{ r.mfe.value }}</span><span v-if="r.mfe.interval" class="iv">{{ r.mfe.interval }}</span></td>
              <td class="num"><span class="v">{{ r.nPath }}</span><span v-if="r.small && r.nPath > 0" class="iv">мало данных</span><span v-if="r.pathShare" class="iv">{{ r.pathShare }}</span></td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-if="anySmall" class="foot">Мало данных: меньше 30 сделок с известным путём цены, интервал широкий.</p>
      <p class="foot">{{ MFE_EXPLAIN }}</p>

      <h4 class="subsub">Доля забранного по номеру сделки</h4>
      <UiBandChart v-if="chart" class="chart" :points="chart.points" :label="chartLabel" x-label="Сделка" :height="132" :format="(v: number) => pctText(v)" />
      <p v-else class="foot">Ряд появится, когда наберётся 30 сделок с известным путём цены.</p>
    </template>
    <p v-else class="state" :class="`k-${view.kind}`" :role="view.kind === 'computing' ? 'status' : undefined">{{ view.message }}</p>
  </section>
</template>

<style scoped>
.blk { min-width: 0; background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: var(--space-4); }
.blk > * { min-width: 0; }
.head { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-2); margin-bottom: var(--space-2); }
.title { margin: 0; margin-right: auto; font-size: 14px; font-weight: 600; }
.state { margin: 0; padding: var(--space-2) 0; font-size: 13px; color: var(--muted); line-height: 1.5; }
.r4-empty { padding-bottom: var(--space-3); }
.ver { margin: 0 0 4px; font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.reason { color: var(--muted); }
.verdict { margin: 0 0 var(--space-3); font-size: 13px; line-height: 1.5; color: var(--text); max-width: 72ch; text-wrap: pretty; }
.lead, .foot { margin: 0 0 var(--space-3); font-size: 12px; line-height: 1.5; color: var(--muted); max-width: 68ch; }
.foot { margin: var(--space-2) 0 0; }
.sub { margin: var(--space-4) 0 var(--space-2); padding-top: var(--space-4); border-top: 1px solid var(--line); font-size: 12px; font-weight: 600; }
.subsub { margin: var(--space-4) 0 var(--space-2); font-size: 12px; font-weight: 600; }

.mx-wrap { min-width: 0; }
.mx { min-width: 0; font-size: 12px; font-variant-numeric: tabular-nums; }
.mx-head, .mrow { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); min-width: 0; }
.mx-head > * { min-width: 0; padding: 0 var(--space-2) 4px; font-size: 11px; color: var(--muted); }
.mrow { border-top: 1px solid var(--line); }
.mrow > * { padding: 6px var(--space-2); min-width: 0; }
.mh { min-width: 0; font-weight: 600; line-height: 1.4; overflow-wrap: anywhere; }
.num { text-align: right; }
.mv { font-size: 15px; font-weight: 600; }
.mv.pos { color: var(--pos); }
.mv.neg { color: var(--neg); }
.mi { font-size: 11px; color: var(--muted); line-height: 1.4; }
.mnote { display: block; margin-top: 2px; font-size: 11px; color: var(--muted); line-height: 1.4; text-align: right; }
.pair { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 4px var(--space-3); }
.pv { display: inline-flex; align-items: baseline; gap: 4px; }
.psep { color: var(--muted); }
.pl { font-size: 11px; color: var(--muted); }
.px { font-size: 13px; font-weight: 600; font-variant-numeric: tabular-nums; }
.r4-foot { display: flex; flex-wrap: wrap; gap: 4px var(--space-4); }
.r4-foot .foot { margin: 0; }

.mrow { animation: r4-in var(--dur-enter) var(--ease-out) both; animation-delay: calc(var(--i, 0) * 30ms); }
@keyframes r4-in {
  from { opacity: 0; transform: translateY(4px); }
}

@media (hover: hover) and (pointer: fine) {
  .mrow { transition: background-color var(--dur-fast) ease; }
  .mrow:hover { background: var(--panel2); }
}

.scroll { overflow-x: auto; }
.tbl { width: 100%; border-collapse: collapse; font-size: 12px; font-variant-numeric: tabular-nums; }
.tbl th, .tbl td { padding: 6px var(--space-2); border-top: 1px solid var(--line); text-align: left; vertical-align: top; font-weight: 400; }
.tbl thead th { border-top: 0; padding-top: 0; font-size: 11px; color: var(--muted); }
.tbl th.wk { white-space: nowrap; }
.tbl .num { text-align: right; }
.v { display: block; color: var(--text); }
.big { font-size: 15px; font-weight: 600; }
.iv { display: block; font-size: 11px; color: var(--muted); }

.sr { position: absolute; width: 1px; height: 1px; margin: -1px; padding: 0; overflow: hidden; clip-path: inset(50%); white-space: nowrap; border: 0; }

/* узкий экран: строка метрик становится блоком с подписями полей, шапка скрыта тем же приёмом, что в VsPolicy */
@media (max-width: 640px) {
  .mx-head { position: absolute; width: 1px; height: 1px; margin: -1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
  .mx-head, .mrow { grid-template-columns: minmax(0, 1fr); }
  .mrow { padding: var(--space-2) 0; }
  .mrow > * { padding: 2px var(--space-2); text-align: left; }
  .mrow > .mh { padding-bottom: 4px; }
  .mrow > .cell { display: grid; grid-template-columns: 9.5em minmax(0, 1fr); gap: var(--space-2); align-items: baseline; }
  .mrow > .cell::before { content: attr(data-label); color: var(--muted); font-size: 11px; }
  .mrow > .cell > .mnote { grid-column: 2; }
  .pair, .mnote { justify-content: flex-start; text-align: left; }
  .r4-foot { flex-direction: column; gap: 2px; }
  .sub { padding-top: var(--space-3); }
}

@media (prefers-reduced-motion: reduce) {
  .mrow { animation-name: r4-fade; animation-delay: 0s; }
  @keyframes r4-fade { from { opacity: 0; } }
}

@media (max-width: 480px) {
  .blk { padding: var(--space-3); }
  .tbl th, .tbl td { padding-inline: 4px; }
  .mrow > * { padding-inline: 4px; }
}
</style>
