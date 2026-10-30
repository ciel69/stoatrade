<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useTradingStore, type TradingMode } from '@/entities/trading'
import { useLive } from '@/shared/api'
import { UiSegment, UiStatusDot, type DotState } from '@/shared/ui'

const MODE_OPTIONS: ReadonlyArray<{ value: TradingMode; label: string }> = [
  { value: 'paper', label: 'бумага' },
  { value: 'live', label: 'live' },
  { value: 'testnet', label: 'testnet' },
  { value: 'all', label: 'все' },
]

const TABS = [{ to: '/', label: 'Обзор' }, { to: '/decisions', label: 'Решения' }, { to: '/trades', label: 'Сделки' }] as const

const trading = useTradingStore()
const { link } = useLive()
onMounted(() => trading.start())

const dot = computed<DotState>(() => ({ open: 'ok', connecting: 'warn', closed: 'bad' } as const)[link.value])
const linkText = computed(() => ({ open: 'связь есть', connecting: 'подключение…', closed: 'нет связи' } as const)[link.value])
const items = computed(() => (trading.snapshot?.platform.items ?? []).filter((i) => i.id === 'exec' || i.id === 'net'))
const dotOf = (level: string | null): DotState => (level === 'ok' ? 'ok' : level === 'warn' ? 'warn' : level === 'bad' ? 'bad' : 'idle')
const platform = computed(() => trading.snapshot?.platform ?? null)
</script>

<template>
  <header class="bar">
    <h1 class="logo">open-trader</h1>
    <nav class="tabs" aria-label="Разделы">
      <RouterLink v-for="t in TABS" :key="t.to" :to="t.to" class="tab">{{ t.label }}</RouterLink>
    </nav>
    <UiSegment :model-value="trading.mode" :options="MODE_OPTIONS" label="Режим" @update:model-value="trading.setMode" />
    <span v-if="platform" class="plat" :class="{ off: platform.state !== 'running' }">{{ platform.state === 'running' ? `платформа: ${platform.mode}` : 'платформа остановлена' }}</span>
    <span class="grow" />
    <span v-for="i in items" :key="i.id" class="chipline"><UiStatusDot :state="dotOf(i.level)" :label="i.label" /> {{ i.label.toLowerCase() }} {{ i.value }}</span>
    <span class="chipline" role="status"><UiStatusDot :state="dot" :label="linkText" /> {{ linkText }}</span>
  </header>
</template>

<style scoped>
.bar { display: flex; align-items: center; gap: var(--space-4); padding: var(--space-3) var(--space-5); border-bottom: 1px solid var(--line); flex-wrap: wrap; }
.logo { margin: 0; font-size: 17px; letter-spacing: -0.02em; }
.tabs { display: flex; gap: var(--space-1); }
.tab { padding: 5px 12px; border-radius: var(--radius-sm); color: var(--muted); font-size: 13px; text-decoration: none; transition: color var(--dur-fast) ease, background-color var(--dur-fast) ease; }
.tab.router-link-exact-active { background: var(--accent-bg); color: var(--accent); }
@media (hover: hover) and (pointer: fine) {
  .tab:not(.router-link-exact-active):hover { color: var(--text); }
}
.plat { font-size: 12px; color: var(--muted); }
.plat.off { color: var(--warn); }
.grow { flex: 1 1 0; }
.chipline { display: inline-flex; align-items: center; gap: var(--space-2); font-size: 12px; color: var(--muted); }
</style>
