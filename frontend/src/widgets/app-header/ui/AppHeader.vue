<script setup lang="ts">
import { onMounted } from 'vue'
import { useTradingStore, type TradingMode } from '@/entities/trading'
import { UiSegment } from '@/shared/ui'
import ThemeToggle from './ThemeToggle.vue'

/** Шапка: переключатель режима слева, справа слот `status` (индикатор состояния платформы подставляет app-слой: виджеты друг друга не импортируют). */
const MODE_OPTIONS: ReadonlyArray<{ value: TradingMode; label: string }> = [
  { value: 'paper', label: 'бумага' },
  { value: 'live', label: 'реальные' },
  { value: 'testnet', label: 'тест-биржа' },
  { value: 'all', label: 'все' },
]

const trading = useTradingStore()
onMounted(() => trading.start())
</script>

<template>
  <header class="bar">
    <UiSegment :model-value="trading.mode" :options="MODE_OPTIONS" label="Режим" @update:model-value="trading.setMode" />
    <span class="grow" />
    <ThemeToggle />
    <slot name="status" />
  </header>
</template>

<style scoped>
.bar { display: flex; align-items: center; gap: var(--space-4); padding: var(--space-3) var(--space-5); border-bottom: 1px solid var(--line); flex-wrap: wrap; }
@media (max-width: 720px) {
  .bar { padding-left: 58px; }        /* место под кнопку боковой колонки */
}
.grow { flex: 1 1 0; }
</style>
