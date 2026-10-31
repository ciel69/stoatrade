<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { coinKey, useMarketStore } from '@/entities/market'
import { useIdeaStore } from '@/entities/idea'
import { useTradingStore } from '@/entities/trading'
import { CoinPanel } from '@/widgets/market/coin-panel'
import { MarketMap } from '@/widgets/market/market-map'
import { NewsFeed } from '@/widgets/market/news-feed'
import { useNewsStore } from '@/entities/headline'
import { UiSidePanel } from '@/shared/ui'

/**
 * Страница «Рынок»: тепловая карта, лента новостей и панель выбранной монеты, которая выезжает справа поверх страницы (карта не сдвигается). Выбор хранится в адресе (?coin=SOL);
 * Esc, крестик и повторный клик закрывают панель.
 * Планы и идеи нужны карте для ободков и панели для ссылок, поэтому подписываем и их.
 */
const route = useRoute()
const router = useRouter()
onMounted(() => { useMarketStore().start(); useIdeaStore().start(); useTradingStore().start(); useNewsStore().start() })
const selected = computed(() => (typeof route.query.coin === 'string' && route.query.coin ? coinKey(route.query.coin) : null))

function pick(sym: string): void {
  void router.replace({ query: selected.value === sym ? {} : { coin: sym } })
}
/** Монета из разметки новости: выбирает её (без повторного клика, который закрыл бы панель). */
function pickCoin(sym: string): void {
  void router.replace({ query: { coin: coinKey(sym) } })
}
function close(): void {
  void router.replace({ query: {} })
}
</script>

<template>
  <main class="page">
    <MarketMap :selected="selected" @select="pick" />
    <NewsFeed :coin="selected" @select-coin="pickCoin" />
    <UiSidePanel :open="!!selected" label="Монета" @close="close">
      <CoinPanel v-if="selected" :sym="selected" @close="close" />
    </UiSidePanel>
  </main>
</template>

<style scoped>
.page { max-width: 1480px; margin: 0 auto; padding: var(--space-5); display: grid; gap: var(--space-4); }
</style>
