<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useActionStore } from '@/entities/action'
import { HeadlinePanel, isNewsMock, useNewsStore } from '@/entities/headline'
import { coinKey } from '@/entities/market'
import { NewsFeed } from '@/widgets/market/news-feed'
import { NewsStatus, NewsStatusPanel } from '@/widgets/market/news-status'
import { UiSidePanel } from '@/shared/ui'

/**
 * Страница «Рынок: новости»: сверху одна фраза о состоянии новостей (кнопка «Подробности» открывает читателя, источники и календарь боковой панелью, ?panel=status), ниже лента
 * с разметкой читателя; новость открывается боковой панелью (?news=ID). Монета в разметке ведёт на карту и её панель (/market?coin=); ?coin=SOL включает фильтр ленты по монете.
 * Отладка без сервера: ?newsmock=mock|mock-reader-off|mock-source-down|mock-blackout|mock-limit (реальных команд в этих режимах не отправляется).
 */
const route = useRoute()
const router = useRouter()
const store = useNewsStore()
const action = useActionStore()
onMounted(() => {
  store.start()
  if (isNewsMock(new URLSearchParams(location.search).get('newsmock'))) action.mockNews()
})

const coin = computed(() => (typeof route.query.coin === 'string' && route.query.coin ? coinKey(route.query.coin) : null))
const newsRef = computed(() => (typeof route.query.news === 'string' && route.query.news ? route.query.news : null))
const item = computed(() => store.snapshot?.items.find((i) => i.ref === newsRef.value) ?? null)
const statusOpen = computed(() => route.query.panel === 'status' && !item.value)

function pickNews(ref: string): void {
  void router.replace({ query: { ...route.query, panel: undefined, news: newsRef.value === ref ? undefined : ref } })
}
function close(): void {
  void router.replace({ query: { ...route.query, news: undefined, panel: undefined } })
}
function toggleStatus(): void {
  void router.replace({ query: { ...route.query, news: undefined, panel: statusOpen.value ? undefined : 'status' } })
}
function toMap(sym: string): void {
  void router.push({ path: '/market', query: { coin: coinKey(sym) } })
}
</script>

<template>
  <main class="page">
    <NewsStatus :open="statusOpen" @details="toggleStatus" />
    <NewsFeed :coin="coin" :selected="newsRef" @select-coin="toMap" @select="pickNews" />
    <UiSidePanel :open="!!item || statusOpen" :label="item ? 'Новость' : 'Состояние новостей'" @close="close">
      <HeadlinePanel v-if="item" :key="item.ref" :item="item" @close="close" @select-coin="toMap" />
      <NewsStatusPanel v-else-if="statusOpen" @close="close" />
    </UiSidePanel>
  </main>
</template>
