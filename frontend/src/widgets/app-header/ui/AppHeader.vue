<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useControlStore } from '@/entities/control'
import { useTradingStore, type TradingMode } from '@/entities/trading'
import { fixed, signed } from '@/shared/lib'
import { UiSegment } from '@/shared/ui'
import ThemeToggle from './ThemeToggle.vue'

/** Шапка: переключатель режима слева, право слота `status` (сводка состояния). Ключевые цифры — в постоянно видимой полосе метрик. */
const MODE_OPTIONS: ReadonlyArray<{ value: TradingMode; label: string }> = [
  { value: 'paper', label: 'бумага' },
  { value: 'live', label: 'реальные' },
  { value: 'testnet', label: 'тест-биржа' },
  { value: 'all', label: 'все' },
]
const SESSION: Record<string, string> = { live: 'LIVE', paper: 'PAPER', testnet: 'TESTNET', observe: 'OBSERVE' }

const trading = useTradingStore()
const control = useControlStore()
onMounted(() => {
  trading.start()
  control.start()
})

const money = computed(() => trading.snapshot?.money ?? null)
const dayPnl = computed(() => money.value?.pnl_today ?? null)
const dayTone = computed(() => (dayPnl.value != null && dayPnl.value > 0 ? 'pos' : dayPnl.value != null && dayPnl.value < 0 ? 'neg' : ''))
const platform = computed(() => control.ctl?.platform ?? null)
const running = computed(() => platform.value?.state === 'running' || (platform.value?.state == null && trading.snapshot?.platform.trading === true))
const sessionMode = computed(() => platform.value?.mode ?? trading.snapshot?.platform.mode ?? null)
const sessionWord = computed(() => (running.value ? SESSION[sessionMode.value ?? ''] ?? sessionMode.value ?? '—' : platform.value?.state === 'starting' ? 'ЗАПУСК' : 'НЕТ СЕССИИ'))
const sessionKind = computed(() => (running.value && platform.value?.session === 'day' ? ' · день' : ''))
const sessionTone = computed(() => (running.value ? (sessionMode.value === 'live' ? 'live' : sessionMode.value === 'testnet' ? 'testnet' : sessionMode.value === 'paper' ? 'paper' : '') : 'off'))
const riskLevel = computed(() => control.ctl?.risk.level ?? null)
const riskName = computed(() => control.ctl?.risk.name ?? null)
const riskTone = computed(() => (riskLevel.value != null && riskLevel.value >= 4 ? 'high' : ''))
const sessionTitle = computed(() => (running.value ? 'Фактически запущенная сессия, не фильтр просмотра.' : 'Сессия не запущена.'))
</script>

<template>
  <header class="bar">
    <dl class="metrics" aria-label="Ключевые показатели">
      <div class="metric" title="Капитал на счёте (equity) в выбранном режиме.">
        <dt>Депозит</dt>
        <dd>{{ fixed(money?.equity, 2) }} <small>USDT</small></dd>
      </div>
      <div class="metric" title="Суммарный риск до стопов по открытым позициям и ждущим заявкам.">
        <dt>Под риском</dt>
        <dd>{{ fixed(money?.under_risk, 2) }} <small>USDT</small></dd>
      </div>
      <div class="metric result" :class="dayTone" title="Закрытый результат сделок за сегодня: частичные фиксации учтены, плавающий PnL открытых позиций не входит.">
        <dt>Результат дня</dt>
        <dd>{{ signed(dayPnl) }} <small>USDT</small></dd>
      </div>
      <div class="metric session" :class="`s-${sessionTone}`" :title="sessionTitle">
        <dt>Сессия</dt>
        <dd><span class="dot" aria-hidden="true" />{{ sessionWord }}{{ sessionKind }}</dd>
      </div>
      <div class="metric risk" :class="riskTone" title="Активный уровень риска (1–5).">
        <dt>Уровень</dt>
        <dd>{{ riskLevel ?? '—' }}<span v-if="riskName" class="name"> · {{ riskName }}</span></dd>
      </div>
    </dl>
    <UiSegment :model-value="trading.mode" :options="MODE_OPTIONS" label="Просмотр" @update:model-value="trading.setMode" />
    <span class="grow" />
    <ThemeToggle />
    <slot name="status" />
  </header>
</template>

<style scoped>
.bar { display: flex; align-items: center; gap: var(--space-4); padding: var(--space-3) var(--space-5); border-bottom: 1px solid var(--line); flex-wrap: wrap; }
.metrics { display: flex; align-items: baseline; gap: var(--space-5); margin: 0; min-width: 0; }
.metric { display: grid; gap: 2px; min-width: 0; }
.metric dt { font-size: 10px; line-height: 1.2; font-weight: 600; letter-spacing: 0.03em; color: var(--muted); text-transform: uppercase; white-space: nowrap; }
.metric dd { margin: 0; font-size: 14px; line-height: 1.25; font-weight: 700; font-variant-numeric: tabular-nums; white-space: nowrap; color: var(--text); }
.metric small { font-size: 10px; font-weight: 400; color: var(--muted); }
.session dd { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; }
.dot { width: 6px; height: 6px; border-radius: 50%; background: var(--muted); flex: none; }
.s-live { color: var(--neg); }
.s-live .dot { background: var(--neg); }
.s-testnet { color: var(--warn); }
.s-testnet .dot { background: var(--warn); }
.s-paper { color: var(--accent); }
.s-paper .dot { background: var(--accent); }
.s-off { color: var(--muted); }
.risk.high dd { color: var(--warn); }
.risk .name { color: var(--muted); font-weight: 500; }
.result.pos dd { color: var(--pos); }
.result.neg dd { color: var(--neg); }
.grow { flex: 1 1 0; }
@media (max-width: 1280px) {
  .bar { gap: var(--space-3); }
  .metrics { gap: var(--space-4); }
  .risk .name { display: none; }
}
@media (max-width: 900px) {
  .metrics { order: 5; flex: 1 0 100%; justify-content: space-between; gap: var(--space-3); padding-top: var(--space-2); border-top: 1px solid var(--line); }
}
@media (max-width: 720px) {
  .bar { padding-left: 58px; }        /* место под кнопку мобильного меню */
}
@media (max-width: 520px) {
  .bar :deep(.theme) { display: none; }
  .bar :deep(.ps .txt) { display: none; }
  .metrics { flex-wrap: wrap; gap: var(--space-2); }
  .metric dt { font-size: 9px; }
  .metric dd { font-size: 12px; }
  .metric small { font-size: 9px; }
}
</style>
