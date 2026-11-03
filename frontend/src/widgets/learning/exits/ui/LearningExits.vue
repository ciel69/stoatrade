<script setup lang="ts">
import { computed } from 'vue'
import { blockView, type ExitsBlock } from '@/entities/learning'
import { LEARN_SMALL } from '@/shared/lib'
import { UiBandChart, UiChip } from '@/shared/ui'
import { CAPTURE_EXPLAIN, MFE_EXPLAIN, exitRows, pctText, pickSeries, windowText } from '../lib/exits'

/**
 * Блок «Выходы»: какую долю лучшего хода цены забирает выход и каким был этот ход, по окнам 50, 200 и за весь период,
 * ряд доли забранного по номеру сделки. Рядом доля сделок с известным путём цены. Только чтение, без переключателей.
 */
const props = defineProps<{ block: ExitsBlock; computingForS?: number }>()

const view = computed(() => blockView(props.block, props.computingForS ?? 0))
const hasData = computed(() => view.value.kind === 'data')
const rows = computed(() => exitRows(props.block.windows ?? {}))
const chart = computed(() => pickSeries(props.block.series ?? {}))
const anySmall = computed(() => rows.value.some((r) => r.small && r.nPath > 0))
const chartLabel = computed(() => (chart.value ? `Доля забранного, ${windowText(chart.value.key).toLowerCase()} сделок` : 'Доля забранного'))
</script>

<template>
  <section class="blk" aria-labelledby="learn-exits-h" data-block="exits">
    <header class="head">
      <h2 id="learn-exits-h" class="title">{{ block.title }}</h2>
      <UiChip v-if="hasData && view.few" tone="warn">{{ LEARN_SMALL() }}</UiChip>
    </header>

    <p v-if="!hasData" class="state" :class="`k-${view.kind}`" :role="view.kind === 'computing' ? 'status' : undefined">{{ view.message }}</p>

    <template v-else>
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

      <h3 class="sub">Доля забранного по номеру сделки</h3>
      <UiBandChart v-if="chart" class="chart" :points="chart.points" :label="chartLabel" x-label="Сделка" :height="132" :format="(v: number) => pctText(v)" />
      <p v-else class="foot">Ряд появится, когда наберётся 30 сделок с известным путём цены.</p>
    </template>
  </section>
</template>

<style scoped>
.blk { min-width: 0; background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: var(--space-4); }
.head { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-2); margin-bottom: var(--space-2); }
.title { margin: 0; margin-right: auto; font-size: 14px; font-weight: 600; }
.lead, .foot { margin: 0 0 var(--space-3); font-size: 12px; line-height: 1.5; color: var(--muted); max-width: 68ch; }
.foot { margin: var(--space-2) 0 0; }
.state { margin: 0; padding: var(--space-4) 0; font-size: 13px; color: var(--muted); }
.sub { margin: var(--space-4) 0 var(--space-2); font-size: 12px; font-weight: 600; }

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

@media (max-width: 480px) {
  .blk { padding: var(--space-3); }
  .tbl th, .tbl td { padding-inline: 4px; }
}
</style>
