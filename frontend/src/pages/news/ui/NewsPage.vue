<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { HeadlinePanel, useNewsStore } from '@/entities/headline'
import { coinKey } from '@/entities/market'
import { NewsFeed } from '@/widgets/market/news-feed'
import { UiSidePanel } from '@/shared/ui'

/**
 * Страница «Рынок: новости»: лента новостей с разметкой читателя; новость открывается боковой панелью (?news=ID). Монета в разметке ведёт на карту и её панель (/market?coin=);
 * ?coin=SOL на этой странице включает фильтр ленты по монете (ссылка есть в панели монеты).
 */
const route = useRoute()
const router = useRouter()
const store = useNewsStore()
onMounted(() => store.start())

const coin = computed(() => (typeof route.query.coin === 'string' && route.query.coin ? coinKey(route.query.coin) : null))
const newsRef = computed(() => (typeof route.query.news === 'string' && route.query.news ? route.query.news : null))
const item = computed(() => store.snapshot?.items.find((i) => i.ref === newsRef.value) ?? null)

function pickNews(ref: string): void {
  void router.replace({ query: { ...route.query, news: newsRef.value === ref ? undefined : ref } })
}
function close(): void {
  void router.replace({ query: { ...route.query, news: undefined } })
}
function toMap(sym: string): void {
  void router.push({ path: '/market', query: { coin: coinKey(sym) } })
}
</script>

<template>
  <main class="page">
    <NewsFeed :coin="coin" :selected="newsRef" @select-coin="toMap" @select="pickNews" />
    <UiSidePanel :open="!!item" label="Новость" @close="close">
      <HeadlinePanel v-if="item" :key="item.ref" :item="item" @close="close" @select-coin="toMap" />
    </UiSidePanel>
  </main>
</template>
