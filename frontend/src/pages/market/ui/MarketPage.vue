<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { coinKey, useMarketStore } from '@/entities/market'
import { useIdeaStore } from '@/entities/idea'
import { useTradingStore } from '@/entities/trading'
import { CoinPanel } from '@/widgets/market/coin-panel'
import { ConsolidatedMarket } from '@/widgets/market/consolidated'
import { MarketMap } from '@/widgets/market/market-map'
import { UiSegment, UiSidePanel } from '@/shared/ui'

/**
 * Страница «Рынок»: карта монет или сводный стакан всех бирж. Карта и панель монеты работают как раньше (выбор в ?coin=);
 * сводный стакан выбирает монету в ?cm= и открывается по прямой ссылке, вид переключается сегментом.
 */
const route = useRoute()
const router = useRouter()
onMounted(() => { useMarketStore().start(); useIdeaStore().start(); useTradingStore().start() })

type MarketView = 'map' | 'cm'
const views: ReadonlyArray<{ value: MarketView; label: string }> = [
  { value: 'map', label: 'Карта монет' },
  { value: 'cm', label: 'Сводный стакан' },
]
const view = computed<MarketView>(() => (typeof route.query.cm === 'string' && route.query.cm ? 'cm' : route.query.view === 'cm' ? 'cm' : 'map'))
const selected = computed(() => (typeof route.query.coin === 'string' && route.query.coin ? coinKey(route.query.coin) : null))
const cmSelected = computed(() => (typeof route.query.cm === 'string' && route.query.cm ? route.query.cm.toUpperCase() : null))

function setView(next: MarketView): void {
  const q = { ...route.query }
  delete q.cm
  if (next === 'cm') q.view = 'cm'
  else delete q.view
  void router.replace({ query: q })
}
function pick(sym: string): void {
  void router.replace({ query: selected.value === sym ? {} : { coin: sym } })
}
function close(): void {
  void router.replace({ query: {} })
}
function pickCm(sym: string): void {
  void router.replace({ query: { ...route.query, view: 'cm', cm: sym } })
}

</script>

<template>
  <main class="page">
    <div class="viewbar">
      <UiSegment :model-value="view" :options="views" label="Вид рынка" @update:model-value="setView" />
    </div>
    <template v-if="view === 'map'">
      <MarketMap :selected="selected" @select="pick" />
      <UiSidePanel :open="!!selected" label="Монета" @close="close">
        <CoinPanel v-if="selected" :key="selected" :sym="selected" @close="close" />
      </UiSidePanel>
    </template>
    <ConsolidatedMarket v-else :selected="cmSelected" @select="pickCm" />
  </main>
</template>

<style scoped>
.viewbar { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-2); min-width: 0; }
</style>
