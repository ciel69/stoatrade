<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { TradeDetailPanel, useTradingStore } from '@/entities/trading'
import { PnlBars } from '@/widgets/pnl-bars'
import { PnlChart } from '@/widgets/pnl-chart'
import { TradesBreakdown } from '@/widgets/trades-breakdown'
import { TradesList } from '@/widgets/trades-list'
import { UiSidePanel } from '@/shared/ui'

/** Страница «Сделки»: кривая результата, история и разбивки. Разбор сделки открывается боковой панелью поверх страницы; выбор хранится в адресе (?trade=ID), повторный клик закрывает. */
const route = useRoute()
const router = useRouter()
const trading = useTradingStore()
const selected = computed(() => (typeof route.query.trade === 'string' && route.query.trade ? route.query.trade : null))
const trade = computed(() => trading.trades?.items.find((t) => t.id === selected.value) ?? null)
function pick(id: string): void {
  void router.replace({ query: selected.value === id ? {} : { trade: id } })
}
function close(): void {
  void router.replace({ query: {} })
}
</script>

<template>
  <main class="page">
    <PnlChart />
    <div class="work">
      <TradesList :selected="selected" @select="pick" />
      <div class="col">
        <PnlBars />
        <TradesBreakdown />
      </div>
    </div>
    <UiSidePanel :open="!!trade" label="Сделка" @close="close">
      <TradeDetailPanel v-if="trade" :key="trade.id" :trade="trade" @close="close" />
    </UiSidePanel>
  </main>
</template>

<style scoped>
.page { max-width: 1180px; margin: 0 auto; padding: var(--space-5); display: grid; gap: var(--space-4); }
.work { display: grid; grid-template-columns: minmax(0, 3fr) minmax(300px, 2fr); gap: var(--space-4); align-items: start; }
.col { display: grid; gap: var(--space-4); min-width: 0; }
@media (max-width: 900px) {
  .work { grid-template-columns: minmax(0, 1fr); }
}
</style>
