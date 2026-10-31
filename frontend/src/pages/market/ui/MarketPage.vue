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

/**
 * Страница «Рынок»: тепловая карта и панель выбранной монеты. Выбор хранится в адресе (?coin=SOL); Esc, крестик и повторный клик закрывают панель.
 * Широкий экран: панель рядом с картой и остаётся на месте при прокрутке. Узкий: панель поверх страницы на весь экран.
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
  <main class="page" :class="{ open: selected }">
    <div class="main">
      <MarketMap :selected="selected" @select="pick" />
      <NewsFeed :coin="selected" @select-coin="pickCoin" />
    </div>
    <Transition name="pd">
      <div v-if="selected" :key="selected" class="side">
        <CoinPanel :sym="selected" @close="close" />
      </div>
    </Transition>
    <div v-if="selected" class="scrim" @click="close" />
  </main>
</template>

<style scoped>
.page { max-width: 1480px; margin: 0 auto; padding: var(--space-5); display: grid; gap: var(--space-4); align-items: start; }
.page.open { grid-template-columns: minmax(0, 1fr) minmax(340px, 420px); }
.main { display: grid; gap: var(--space-4); min-width: 0; }
.side { position: sticky; top: var(--space-4); height: calc(100vh - var(--space-4) * 2); display: flex; flex-direction: column; min-height: 0; }
.side > :deep(.panel) { flex: 1 1 auto; }
.scrim { display: none; }
@media (max-width: 1100px) {
  .page.open { grid-template-columns: minmax(0, 1fr); }
  .side { position: fixed; z-index: 45; inset: var(--space-3); height: auto; }
  .scrim { display: block; position: fixed; z-index: 44; inset: 0; background: rgba(0, 0, 0, 0.5); }
}
</style>
