<script setup lang="ts">
import { computed } from 'vue'
import { blockView, type EconomicsBlock, type LearningBlock } from '@/entities/learning'
import { UiBandChart, UiCard } from '@/shared/ui'
import { ECON_CHART, ECON_DERIVED, ECON_FEW, ECON_FEW_NOTE, ECON_SCOPE, economicsView, spendText } from '../lib/economics'

/**
 * Блок «Экономика»: сколько стоит работа мозга в токенах и USD модели и во что это обходится на сделку и на 1 R.
 * Суммы PnL в USDT и капитал не показываются (Q7): результат только в R. Отношения сумм без интервала, с пометкой «расчётная величина».
 */
const props = defineProps<{ block: LearningBlock }>()

const view = computed(() => blockView(props.block))
const data = computed(() => (view.value.kind === 'data' ? economicsView(props.block as EconomicsBlock) : null))
</script>

<template>
  <UiCard :title="block.title" class="ec">
    <Transition name="ec-fade" mode="out-in" appear>
      <div v-if="data" key="data" class="in">
        <p class="scope">{{ ECON_SCOPE }}</p>

        <dl class="sum">
          <div class="it">
            <dt>Расход модели</dt>
            <dd class="val">{{ data.totals.spend }}</dd>
            <dd class="sub">{{ data.totals.calls }}</dd>
          </div>
          <div class="it">
            <dt>Токены</dt>
            <dd class="val">{{ data.totals.tokensIn }} <span class="u">вход</span></dd>
            <dd class="sub">{{ data.totals.tokensOut }} выход</dd>
          </div>
          <div class="it">
            <dt>Результат</dt>
            <dd class="val" :class="data.totals.rTone">{{ data.totals.r }}</dd>
            <dd class="sub">сделок: {{ data.totals.trades }}</dd>
          </div>
          <div class="it">
            <dt>Расход на 1 R</dt>
            <dd class="val">{{ data.totals.perR }}</dd>
            <dd class="sub">{{ data.totals.perRNote }}</dd>
          </div>
        </dl>

        <p class="signal">{{ data.signal }}</p>

        <figure v-if="data.chart.length > 1" class="fig">
          <figcaption>{{ ECON_CHART }}</figcaption>
          <UiBandChart :points="data.chart" :label="ECON_CHART" x-label="Неделя" unit="USD" :format="spendText" :height="96" />
        </figure>

        <table class="tbl">
          <caption class="sr">Расход и результат по неделям</caption>
          <thead>
            <tr>
              <th scope="col" class="l">Неделя</th>
              <th scope="col">Расход</th>
              <th scope="col" class="wide">Сделок</th>
              <th scope="col" class="wide">Результат</th>
              <th scope="col">На сделку</th>
              <th scope="col">На 1 R</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in data.rows" :key="r.key">
              <th scope="row" class="l">
                <div class="wk">
                  <span class="wl">{{ r.label }}<span v-if="r.partial" class="mut"> · идёт</span></span>
                  <span class="sub">{{ r.tokens }}</span>
                  <span class="sub narrow">{{ r.tradesText }}<template v-if="r.few"> ({{ ECON_FEW }})</template>, {{ r.r }}</span>
                </div>
              </th>
              <td>{{ r.spend }}</td>
              <td class="wide">{{ r.trades }}<span v-if="r.few" class="mut"> {{ ECON_FEW }}</span></td>
              <td class="wide" :class="r.rTone">{{ r.r }}</td>
              <td :class="{ mut: r.few }">{{ r.perTrade }}</td>
              <td :class="{ mut: r.few }">{{ r.perR }}</td>
            </tr>
          </tbody>
        </table>

        <p class="foot">{{ ECON_DERIVED }}<template v-if="data.anyFew"> {{ ECON_FEW_NOTE }}</template></p>
      </div>
      <p v-else :key="view.kind" class="state" role="status">{{ view.message }}</p>
    </Transition>
  </UiCard>
</template>

<style scoped>
.ec { min-width: 0; }
.in { container-type: inline-size; display: grid; gap: var(--space-3); min-width: 0; }
.scope, .signal, .foot { margin: 0; font-size: 12px; line-height: 1.5; color: var(--muted); }
.signal { color: var(--text); font-size: 13px; }
.state { margin: 0; padding: var(--space-3) 0; font-size: 13px; color: var(--muted); }

.sum { display: grid; grid-template-columns: repeat(auto-fit, minmax(128px, 1fr)); gap: var(--space-3) var(--space-4); margin: 0; }
.it { min-width: 0; display: grid; gap: 2px; align-content: start; }
.it dt { font-size: 11px; color: var(--muted); }
.it dd { margin: 0; }
.val { font-size: 17px; font-weight: 700; letter-spacing: -0.01em; font-variant-numeric: tabular-nums; }
.val .u { font-size: 11px; font-weight: 400; color: var(--muted); letter-spacing: 0; }
.sub { font-size: 11px; line-height: 1.4; color: var(--muted); font-variant-numeric: tabular-nums; }

.fig { margin: 0; display: grid; gap: var(--space-1); min-width: 0; }
.fig figcaption { font-size: 11px; color: var(--muted); }

.tbl { width: 100%; border-collapse: collapse; font-size: 12px; font-variant-numeric: tabular-nums; }
.tbl th, .tbl td { padding: 6px 0 6px var(--space-3); text-align: right; vertical-align: top; font-weight: 400; }
.tbl thead th { padding-top: 0; padding-bottom: var(--space-2); font-size: 11px; color: var(--muted); border-bottom: 1px solid var(--line2); }
.tbl tbody tr + tr > * { border-top: 1px solid var(--line); }
.tbl .l { padding-left: 0; text-align: left; }
.wk { display: grid; gap: 1px; }
.wl { color: var(--text); }
.mut { color: var(--muted); }
.pos { color: var(--pos); }
.neg { color: var(--neg); }
.wide { display: none; }

@container (min-width: 560px) {
  .tbl .wide { display: table-cell; }
  .tbl .narrow { display: none; }
  .tbl { font-size: 13px; }
}

.sr { position: absolute; width: 1px; height: 1px; margin: -1px; padding: 0; overflow: hidden; clip-path: inset(50%); white-space: nowrap; border: 0; }

/* появление блока и смена состояния (например, «Считается…» -> данные): только opacity и сдвиг на 4 px */
.ec-fade-enter-active { transition: opacity var(--dur-enter) var(--ease-out), transform var(--dur-enter) var(--ease-out); }
.ec-fade-leave-active { transition: opacity var(--dur-fast) var(--ease-out); }
.ec-fade-enter-from { opacity: 0; transform: translateY(4px); }
.ec-fade-leave-to { opacity: 0; }

@media (prefers-reduced-motion: reduce) {
  .ec-fade-enter-from { transform: none; }
  .ec-fade-enter-active { transition: opacity var(--dur-enter) ease; }
}
</style>
