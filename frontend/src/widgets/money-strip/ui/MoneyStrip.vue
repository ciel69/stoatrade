<script setup lang="ts">
import { computed } from 'vue'
import { useTradingStore } from '@/entities/trading'
import { UiMeter } from '@/shared/ui'
import { fixed, signed } from '@/shared/lib'

const trading = useTradingStore()
const m = computed(() => trading.snapshot?.money ?? null)
const dayFill = computed(() => (m.value?.day_limit && m.value.day_room != null ? m.value.day_room / m.value.day_limit : 0))
const dayLevel = computed(() => (dayFill.value < 0.1 ? 'bad' : dayFill.value < 0.3 ? 'warn' : 'ok'))
const pnlTone = computed(() => ((m.value?.pnl_today ?? 0) > 0 ? 'up' : (m.value?.pnl_today ?? 0) < 0 ? 'down' : ''))
</script>

<template>
  <section class="money" aria-label="Деньги" :aria-busy="!m">
    <div class="fig">
      <div class="v">{{ fixed(m?.under_risk) }}<small> USDT</small></div>
      <div class="c">под риском</div>
    </div>
    <div class="fig">
      <div class="v">{{ m?.day_room != null ? fixed(Math.min(m.day_room, m.day_limit ?? m.day_room)) : '–' }}<small> из {{ fixed(m?.day_limit) }}</small></div>
      <UiMeter v-if="m" :fill="dayFill" :level="dayLevel" label="запас до дневного лимита" />
      <div class="c">запас до лимита дня</div>
    </div>
    <div class="fig">
      <div class="v" :class="pnlTone">{{ signed(m?.pnl_today) }}<small> USDT</small></div>
      <div class="c">за день<template v-if="m"> · сделок {{ m.trades_today }}</template><template v-if="m?.open_pnl != null"> · открытые {{ signed(m.open_pnl) }}</template></div>
    </div>
    <div class="fig eq">
      <div class="v">{{ fixed(m?.equity) }}<small> USDT</small></div>
      <div class="c">капитал</div>
    </div>
  </section>
</template>

<style scoped>
.money { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: var(--space-5); padding: var(--space-4) var(--space-5); background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); }
.fig { min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.v { font-size: 30px; font-weight: 700; letter-spacing: -0.02em; line-height: 1.1; font-variant-numeric: tabular-nums; }
.v small { font-size: 12px; font-weight: 400; letter-spacing: 0; color: var(--muted); }
.v.up { color: var(--pos); }
.v.down { color: var(--neg); }
.c { font-size: 12px; color: var(--muted); }
.eq .v { color: var(--text); }
@media (max-width: 900px) {
  .money { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--space-4); }
}
</style>
