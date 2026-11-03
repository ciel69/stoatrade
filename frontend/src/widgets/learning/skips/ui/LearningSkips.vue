<script setup lang="ts">
import { computed } from 'vue'
import { blockView, type SkipsBlock } from '@/entities/learning'
import { LEARN_SMALL } from '@/shared/lib'
import { UiBandChart, UiChip } from '@/shared/ui'
import { FREEZE_HINT, MISSED_EXPLAIN, correctPoints, isFreezing, pctText, signalChip, skipRows, totalView } from '../lib/skips'

/**
 * Блок «Пропуски»: доля верных пропусков по неделям с интервалом, рядом число сделок мозга (чтобы рост доли при падении числа сделок был виден),
 * упущенный ход в процентах цены с пояснением (решение Q8). Только чтение.
 */
const props = defineProps<{ block: SkipsBlock; computingForS?: number }>()

const view = computed(() => blockView(props.block, props.computingForS ?? 0))
const hasData = computed(() => view.value.kind === 'data')
const weeks = computed(() => (Array.isArray(props.block.weeks) ? props.block.weeks : []))
const rows = computed(() => skipRows(weeks.value))
const points = computed(() => correctPoints(weeks.value))
const total = computed(() => totalView(props.block.total))
const signal = computed(() => signalChip(props.block.signal ?? null))
const freezing = computed(() => isFreezing(weeks.value, props.block.signal ?? null))
const anySmall = computed(() => rows.value.some((r) => r.small && r.n > 0))
const hasPartial = computed(() => rows.value.some((r) => r.partial))
</script>

<template>
  <section class="blk" aria-labelledby="learn-skips-h" data-block="skips">
    <header class="head">
      <h2 id="learn-skips-h" class="title">{{ block.title }}</h2>
      <UiChip v-if="hasData && signal" :tone="signal.tone">{{ signal.text }}</UiChip>
      <UiChip v-if="hasData && view.few" tone="warn">{{ LEARN_SMALL() }}</UiChip>
    </header>

    <p v-if="!hasData" class="state" :class="`k-${view.kind}`" :role="view.kind === 'computing' ? 'status' : undefined">{{ view.message }}</p>

    <template v-else>
      <p class="lead">Пропуск верный, если после него цена не пошла в сторону входа сверх издержек. Доля верных показывает, не пропускает ли мозг хорошие входы.</p>

      <p v-if="freezing" class="warn" role="note"><span class="mark" aria-hidden="true">!</span>{{ FREEZE_HINT }}</p>

      <dl v-if="total" class="sum">
        <div><dt>Пропусков</dt><dd>{{ total.n }}</dd></div>
        <div><dt>Верных</dt><dd>{{ total.correct.value }}<span v-if="total.correct.interval" class="iv">{{ total.correct.interval }}</span></dd></div>
        <div v-if="total.wrong != null"><dt>Неверных</dt><dd>{{ total.wrong }}</dd></div>
        <div><dt>Упущено в среднем</dt><dd>{{ total.missedMean }}<span class="iv">цены на неверный пропуск</span></dd></div>
        <div><dt>Упущено всего</dt><dd>{{ total.missedSum }}<span class="iv">цены, сумма</span></dd></div>
      </dl>

      <UiBandChart class="chart" :points="points" label="Доля верных пропусков по неделям" x-label="Неделя" :height="132" :format="(v: number) => pctText(v)" empty-text="Оценок по неделям пока нет" />

      <div class="scroll">
        <table class="tbl">
          <caption class="sr">Пропуски и сделки по неделям</caption>
          <thead>
            <tr>
              <th scope="col">Неделя</th>
              <th scope="col" class="num">Верных</th>
              <th scope="col" class="num">Упущено</th>
              <th scope="col" class="num">Пропусков</th>
              <th scope="col" class="num">Сделок</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in rows" :key="r.key" :class="{ part: r.partial }">
              <th scope="row" class="wk">{{ r.label }}<span v-if="r.partial" class="tag">неполная</span></th>
              <td class="num"><span class="v">{{ r.correct.value }}</span><span v-if="r.correct.interval" class="iv">{{ r.correct.interval }}</span></td>
              <td class="num"><span class="v">{{ r.missed.value }}</span><span v-if="r.missed.interval" class="iv">{{ r.missed.interval }}</span></td>
              <td class="num"><span class="v">{{ r.n }}</span><span v-if="r.small && r.n > 0" class="iv">мало данных</span></td>
              <td class="num"><span class="v">{{ r.trades }}</span></td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-if="anySmall || hasPartial" class="foot">
        <template v-if="anySmall">Мало данных: меньше 30 пропусков, интервал широкий. </template>
        <template v-if="hasPartial">Неполная неделя ещё идёт.</template>
      </p>

      <p class="explain"><b>Что такое упущенный ход.</b> {{ MISSED_EXPLAIN }}</p>
    </template>
  </section>
</template>

<style scoped>
.blk { min-width: 0; background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: var(--space-4); }
.head { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-2); margin-bottom: var(--space-2); }
.title { margin: 0; margin-right: auto; font-size: 14px; font-weight: 600; }
.lead, .foot, .explain { margin: 0 0 var(--space-3); font-size: 12px; line-height: 1.5; color: var(--muted); max-width: 68ch; }
.foot { margin: var(--space-2) 0 0; }
.explain { margin: var(--space-3) 0 0; }
.explain b { color: var(--text); font-weight: 600; }
.state { margin: 0; padding: var(--space-4) 0; font-size: 13px; color: var(--muted); }
.warn { display: flex; gap: var(--space-2); align-items: baseline; margin: 0 0 var(--space-3); padding: var(--space-2) var(--space-3); border: 1px solid var(--warn); border-radius: var(--radius-sm); font-size: 12px; line-height: 1.5; color: var(--text); }
.mark { flex: none; width: 16px; height: 16px; border-radius: 50%; border: 1px solid var(--warn); color: var(--warn); font-size: 11px; font-weight: 700; line-height: 14px; text-align: center; }
.chart { margin-bottom: var(--space-3); }

.sum { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: var(--space-3); margin: 0 0 var(--space-3); font-variant-numeric: tabular-nums; }
.sum dt { font-size: 11px; color: var(--muted); }
.sum dd { margin: 0; font-size: 16px; font-weight: 600; }
.sum .iv { font-weight: 400; }

.scroll { overflow-x: auto; }
.tbl { width: 100%; border-collapse: collapse; font-size: 12px; font-variant-numeric: tabular-nums; }
.tbl th, .tbl td { padding: 5px var(--space-2); border-top: 1px solid var(--line); text-align: left; vertical-align: top; font-weight: 400; }
.tbl thead th { border-top: 0; padding-top: 0; font-size: 11px; color: var(--muted); }
.tbl th.wk { white-space: nowrap; }
.tbl .num { text-align: right; }
.v { display: block; color: var(--text); }
.iv { display: block; font-size: 11px; color: var(--muted); }
.part .wk { color: var(--muted); }
.tag { margin-left: var(--space-2); padding: 0 6px; border: 1px solid var(--line2); border-radius: 99px; font-size: 10px; color: var(--muted); }

.sr { position: absolute; width: 1px; height: 1px; margin: -1px; padding: 0; overflow: hidden; clip-path: inset(50%); white-space: nowrap; border: 0; }

@media (max-width: 480px) {
  .blk { padding: var(--space-3); }
  .tbl th, .tbl td { padding-inline: 4px; }
}
</style>
