<script setup lang="ts">
import { PnlBars } from '@/widgets/pnl-bars'
import { PnlChart } from '@/widgets/pnl-chart'
import { TradesBreakdown } from '@/widgets/trades-breakdown'
import { TradesList } from '@/widgets/trades-list'
</script>

<template>
  <main class="page">
    <PnlChart />
    <div class="work">
      <TradesList />
      <div class="col">
        <PnlBars />
        <TradesBreakdown />
      </div>
    </div>
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
