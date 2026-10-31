<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { coinKey, useMarketStore } from '@/entities/market'
import { useIdeaStore } from '@/entities/idea'
import { useTradingStore } from '@/entities/trading'
import { CoinPanel } from '@/widgets/market/coin-panel'
import { MarketMap } from '@/widgets/market/market-map'
import { NewsFeed } from '@/widgets/market/news-feed'
import { HeadlinePanel, useNewsStore } from '@/entities/headline'
import { UiSidePanel } from '@/shared/ui'

/**
 * Страница «Рынок»: тепловая карта и лента новостей; детали открываются боковой панелью справа поверх страницы (контент не сдвигается): монета (?coin=SOL) или новость (?news=ID).
 * Одновременно открыто что-то одно. Esc, крестик и повторный клик закрывают панель. Планы и идеи нужны карте для ободков и панели монеты для ссылок, поэтому подписываем и их.
 */
const route = useRoute()
const router = useRouter()
onMounted(() => { useMarketStore().start(); useIdeaStore().start(); useTradingStore().start(); useNewsStore().start() })
const selected = computed(() => (typeof route.query.coin === 'string' && route.query.coin ? coinKey(route.query.coin) : null))
const newsRef = computed(() => (typeof route.query.news === 'string' && route.query.news ? route.query.news : null))
const news = computed(() => useNewsStore().snapshot?.items.find((i) => i.ref === newsRef.value) ?? null)
const open = computed(() => !!selected.value || !!news.value)

function pick(sym: string): void {
  void router.replace({ query: selected.value === sym ? {} : { coin: sym } })
}
/** Монета из разметки новости: выбирает её (без повторного клика, который закрыл бы панель). */
function pickCoin(sym: string): void {
  void router.replace({ query: { coin: coinKey(sym) } })
}
function pickNews(ref: string): void {
  void router.replace({ query: newsRef.value === ref ? {} : { news: ref } })
}
function close(): void {
  void router.replace({ query: {} })
}
</script>

<template>
  <main class="page">
    <MarketMap :selected="selected" @select="pick" />
    <NewsFeed :coin="selected" :selected="newsRef" @select-coin="pickCoin" @select="pickNews" />
    <UiSidePanel :open="open" :label="news ? 'Новость' : 'Монета'" @close="close">
      <HeadlinePanel v-if="news" :key="news.ref" :item="news" @close="close" @select-coin="pickCoin" />
      <CoinPanel v-else-if="selected" :key="selected" :sym="selected" @close="close" />
    </UiSidePanel>
  </main>
</template>

<style scoped>
.page { max-width: 1480px; margin: 0 auto; padding: var(--space-5); display: grid; gap: var(--space-4); }
</style>
