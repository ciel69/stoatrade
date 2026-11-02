<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { coinKey, useMarketStore } from '@/entities/market'
import { useIdeaStore } from '@/entities/idea'
import { useTradingStore } from '@/entities/trading'
import { CoinPanel } from '@/widgets/market/coin-panel'
import { MarketMap } from '@/widgets/market/market-map'
import { UiSidePanel } from '@/shared/ui'

/**
 * Страница «Рынок: карта»: тепловая карта; детали монеты открываются боковой панелью справа поверх страницы (карта не сдвигается), выбор хранится в адресе (?coin=SOL),
 * Esc, крестик и повторный клик закрывают панель. Планы и идеи нужны карте для ободков и панели монеты для ссылок, поэтому подписываем и их. Новости на соседней вкладке.
 */
const route = useRoute()
const router = useRouter()
onMounted(() => { useMarketStore().start(); useIdeaStore().start(); useTradingStore().start() })
const selected = computed(() => (typeof route.query.coin === 'string' && route.query.coin ? coinKey(route.query.coin) : null))

function pick(sym: string): void {
  void router.replace({ query: selected.value === sym ? {} : { coin: sym } })
}
function close(): void {
  void router.replace({ query: {} })
}
</script>

<template>
  <main class="page">
    <MarketMap :selected="selected" @select="pick" />
    <UiSidePanel :open="!!selected" label="Монета" @close="close">
      <CoinPanel v-if="selected" :key="selected" :sym="selected" @close="close" />
    </UiSidePanel>
  </main>
</template>
