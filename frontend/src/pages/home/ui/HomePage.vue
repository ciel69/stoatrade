<script setup lang="ts">
import { BrainPanel } from '@/widgets/brain-panel'
import { LimitsStrip } from '@/widgets/limits-strip'
import { MoneyStrip } from '@/widgets/money-strip'
import { PnlChart } from '@/widgets/pnl-chart'
import { PositionsBoard } from '@/widgets/positions-board'
</script>

<template>
  <main class="page">
    <MoneyStrip />
    <LimitsStrip />
    <div class="work">
      <div class="col">
        <PositionsBoard />
        <PnlChart />
      </div>
      <BrainPanel />
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
