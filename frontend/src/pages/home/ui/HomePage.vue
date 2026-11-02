<script setup lang="ts">
import { BrainPanel } from '@/widgets/brain-panel'
import { LimitsStrip } from '@/widgets/limits-strip'
import { MoneyStrip } from '@/widgets/money-strip'
import { PendingBoard } from '@/widgets/pending-board'
import { PlansBoard } from '@/widgets/plans-board'
import { PnlChart } from '@/widgets/pnl-chart'
import { PositionsBoard } from '@/widgets/positions-board'
</script>

<template>
  <main class="page">
    <MoneyStrip />
    <LimitsStrip />
    <div class="work">
      <div class="col a">
        <PositionsBoard />
        <PnlChart />
      </div>
      <div class="col b">
        <BrainPanel />
        <PendingBoard />
      </div>
      <div class="col c">
        <PlansBoard />
      </div>
    </div>
  </main>
</template>

<style scoped>
/* Две колонки: позиции и кривая слева, мозг и планы справа; от 1700 px три: планы уходят в свою колонку. */
.work { display: grid; grid-template-columns: minmax(0, 3fr) minmax(300px, 2fr); gap: var(--space-4); align-items: start; }
.col { display: grid; gap: var(--space-4); min-width: 0; }
.a { grid-column: 1; grid-row: 1 / span 2; }
.b { grid-column: 2; grid-row: 1; }
.c { grid-column: 2; grid-row: 2; }
@media (min-width: 1700px) {
  .work { grid-template-columns: minmax(0, 5fr) minmax(0, 4fr) minmax(0, 4fr); }
  .a { grid-row: 1; }
  .c { grid-column: 3; grid-row: 1; }
}
@media (max-width: 900px) {
  .work { grid-template-columns: minmax(0, 1fr); }
  .a, .b, .c { grid-column: 1; grid-row: auto; }
}
</style>
