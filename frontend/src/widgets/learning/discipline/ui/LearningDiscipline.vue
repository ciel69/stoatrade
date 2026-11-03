<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { blockView, type DisciplineBlock } from '@/entities/learning'
import { LEARN_SMALL } from '@/shared/lib'
import { UiBandChart, UiChip } from '@/shared/ui'
import { pctText, signalChip, tagRows, violPoints, weekRows } from '../lib/discipline'

/**
 * Блок «Дисциплина»: доля случаев с нарушением правила по неделям (линия с лентой интервала), таблица недель, частые ошибки.
 * Текущая неделя неполная и помечена. У каждой доли есть n; где случаев меньше 30, стоит «мало данных». Только чтение.
 */
const props = defineProps<{ block: DisciplineBlock; computingForS?: number }>()

const view = computed(() => blockView(props.block, props.computingForS ?? 0))
const hasData = computed(() => view.value.kind === 'data')
const weeks = computed(() => (Array.isArray(props.block.weeks) ? props.block.weeks : []))
const rows = computed(() => weekRows(weeks.value))
const points = computed(() => violPoints(weeks.value))
const tags = computed(() => tagRows({ err_total: Array.isArray(props.block.err_total) ? props.block.err_total : [] }))
const signal = computed(() => signalChip(props.block.signal ?? null))
const anySmall = computed(() => rows.value.some((r) => r.small && r.cases > 0))
const hasPartial = computed(() => rows.value.some((r) => r.partial))
const ready = ref(false)
onMounted(() => requestAnimationFrame(() => { ready.value = true }))        // полосы тегов растут после первой раскладки
</script>

<template>
  <section class="blk" aria-labelledby="learn-disc-h" data-block="discipline">
    <header class="head">
      <h2 id="learn-disc-h" class="title">{{ block.title }}</h2>
      <UiChip v-if="hasData && signal" :tone="signal.tone">{{ signal.text }}</UiChip>
      <UiChip v-if="hasData && view.few" tone="warn">{{ LEARN_SMALL() }}</UiChip>
    </header>

    <p v-if="!hasData" class="state" :class="`k-${view.kind}`" :role="view.kind === 'computing' ? 'status' : undefined">{{ view.message }}</p>

    <template v-else>
      <p class="lead">Доля закрытых сделок, где правило не выполнено. Чем ниже, тем лучше.</p>
      <p v-if="block.note" class="note">{{ block.note }}</p>

      <UiBandChart class="chart" :points="points" label="Доля нарушений правил по неделям" x-label="Неделя" :height="132" :format="(v: number) => pctText(v)" empty-text="Оценок по неделям пока нет" />

      <div class="scroll" tabindex="0" role="region" aria-label="Нарушения и ошибки по неделям">
        <table class="tbl">
          <caption class="sr">Нарушения и ошибки по неделям</caption>
          <thead>
            <tr><th scope="col">Неделя</th><th scope="col" class="num">Нарушения</th><th scope="col" class="num">Хоть одна ошибка</th><th scope="col" class="num">Случаев</th></tr>
          </thead>
          <tbody>
            <tr v-for="r in rows" :key="r.key" :class="{ part: r.partial }">
              <th scope="row" class="wk">{{ r.label }}<span v-if="r.partial" class="tag">неполная</span></th>
              <td class="num"><span class="v">{{ r.viol.value }}</span><span v-if="r.viol.interval" class="iv">{{ r.viol.interval }}</span></td>
              <td class="num"><span class="v">{{ r.err.value }}</span><span v-if="r.err.interval" class="iv">{{ r.err.interval }}</span></td>
              <td class="num"><span class="v">{{ r.cases }}</span><span v-if="r.small && r.cases > 0" class="iv">мало данных</span></td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-if="anySmall || hasPartial" class="foot">
        <template v-if="anySmall">Мало данных: меньше 30 случаев, интервал широкий. </template>
        <template v-if="hasPartial">Неполная неделя ещё идёт, её доля изменится.</template>
      </p>

      <template v-if="tags.length">
        <h3 class="sub">Частые ошибки за период</h3>
        <ul class="tags">
          <li v-for="t in tags" :key="t.tag" class="tg" :class="{ ready }">
            <span class="tn">{{ t.text }}</span>
            <span class="tv"><span class="v">{{ t.value }}</span><span v-if="t.interval" class="iv">{{ t.interval }}</span><span class="iv">{{ t.n }}</span></span>
            <span class="bar" aria-hidden="true"><span class="fill" :style="{ '--share': t.share }" /></span>
          </li>
        </ul>
      </template>
    </template>
  </section>
</template>

<style scoped>
.blk { min-width: 0; background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: var(--space-4); }
.head { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-2); margin-bottom: var(--space-2); }
.title { margin: 0; margin-right: auto; font-size: 14px; font-weight: 600; }
.lead, .note, .foot { margin: 0 0 var(--space-3); font-size: 12px; line-height: 1.5; color: var(--muted); max-width: 68ch; }
.foot { margin: var(--space-2) 0 0; }
.state { margin: 0; padding: var(--space-4) 0; font-size: 13px; color: var(--muted); }
.chart { margin-bottom: var(--space-3); }

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

.sub { margin: var(--space-4) 0 var(--space-2); font-size: 12px; font-weight: 600; }
.tags { margin: 0; padding: 0; list-style: none; display: grid; gap: var(--space-3); }
.tg { display: grid; grid-template-columns: 1fr auto; gap: 2px var(--space-3); align-items: baseline; font-size: 12px; min-width: 0; }
.tn { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tv { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 0 var(--space-2); font-variant-numeric: tabular-nums; text-align: right; }
.tv .v, .tv .iv { display: inline; }
.bar { grid-column: 1 / -1; height: 4px; border-radius: 2px; background: var(--line); overflow: hidden; }
.fill { display: block; height: 100%; background: var(--accent); transform-origin: left center; transform: scaleX(0); }
.ready .fill { transform: scaleX(var(--share)); transition: transform var(--dur-enter) var(--ease-out); }
.tg:nth-child(2) .fill { transition-delay: 40ms; }
.tg:nth-child(3) .fill { transition-delay: 80ms; }
.tg:nth-child(n+4) .fill { transition-delay: 120ms; }

.sr { position: absolute; width: 1px; height: 1px; margin: -1px; padding: 0; overflow: hidden; clip-path: inset(50%); white-space: nowrap; border: 0; }

@media (prefers-reduced-motion: reduce) {
  .fill { transform: scaleX(var(--share)); }
  .ready .fill { transition: none; }
}
@media (max-width: 480px) {
  .blk { padding: var(--space-3); }
}
</style>
