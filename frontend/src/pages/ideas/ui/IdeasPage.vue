<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { defaultTab, IdeaDetailPanel, TAB_LABEL, tabCount, useIdeaStore, type IdeaTab } from '@/entities/idea'
import { IdeasAuto } from '@/widgets/ideas/ideas-auto'
import { IdeasList } from '@/widgets/ideas/ideas-list'
import { UiSegment, UiSidePanel } from '@/shared/ui'

/**
 * Страница «Идеи»: четыре вкладки как на старом дашборде (ждут уровня, стали планом, не сработали, авто-история сканера). Выбор вкладки хранится в адресе (?tab=), вкладка по умолчанию
 * (первая непустая) в адрес не пишется. Данные приходят по WebSocket темой ideas.
 */
const store = useIdeaStore()
const route = useRoute()
const router = useRouter()
onMounted(() => store.start())

const TABS: IdeaTab[] = ['active', 'taken', 'skipped', 'auto']
const snap = computed(() => store.snapshot)
const tab = computed<IdeaTab>(() => {
  const q = route.query.tab
  return typeof q === 'string' && (TABS as string[]).includes(q) ? (q as IdeaTab) : snap.value ? defaultTab(snap.value) : 'active'
})
const options = computed(() => TABS.map((t) => ({ value: t, label: `${TAB_LABEL[t]}${snap.value ? ' ' + tabCount(snap.value, t) : ''}` })))
function pick(t: IdeaTab): void {
  void router.replace({ query: { tab: t } })
}
/** Выбранная идея хранится в адресе (?idea=ID); повторный клик закрывает панель. */
const selectedId = computed(() => (typeof route.query.idea === 'string' && route.query.idea ? route.query.idea : null))
const idea = computed(() => snap.value?.items.find((i) => i.id === selectedId.value) ?? null)
function pickIdea(id: string): void {
  void router.replace({ query: { ...route.query, idea: selectedId.value === id ? undefined : id } })
}
function closeIdea(): void {
  void router.replace({ query: { ...route.query, idea: undefined } })
}
const share = computed(() => (snap.value?.stats.agent.taken_share != null ? `${Math.round(snap.value.stats.agent.taken_share * 100)}%` : '–'))
</script>

<template>
  <main class="page">
    <header class="top">
      <h2 class="title">Идеи</h2>
      <p v-if="snap" class="sum">
        <span><b>{{ snap.stats.agent.total }}</b> идей мозга</span>
        <span><b>{{ share }}</b> стали сделкой</span>
        <span><b>{{ snap.counts.auto_skipped ?? 0 }}</b> событий сканера</span>
        <span title="исход через 15 и 60 минут"><b>{{ snap.pending }}</b> ждут замера</span>
      </p>
      <UiSegment :model-value="tab" :options="options" label="Вкладка идей" @update:model-value="pick" />
    </header>
    <IdeasAuto v-if="tab === 'auto'" />
    <IdeasList v-else :tab="tab" :selected="selectedId" @select="pickIdea" />
    <UiSidePanel :open="!!idea" label="Идея" @close="closeIdea">
      <IdeaDetailPanel v-if="idea" :idea="idea" @close="closeIdea" />
    </UiSidePanel>
  </main>
</template>

<style scoped>
.page { max-width: 1180px; margin: 0 auto; padding: var(--space-5); display: grid; gap: var(--space-4); }
.top { display: flex; align-items: center; flex-wrap: wrap; gap: var(--space-3) var(--space-4); }
.title { margin: 0; font-size: 14px; font-weight: 600; margin-right: auto; }
.sum { display: flex; flex-wrap: wrap; gap: var(--space-4); margin: 0; font-size: 12px; color: var(--muted); }
.sum b { color: var(--text); font-variant-numeric: tabular-nums; }
</style>
