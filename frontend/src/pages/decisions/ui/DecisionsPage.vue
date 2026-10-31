<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { DecisionDetailPanel, useTradingStore } from '@/entities/trading'
import { DecisionsFeed } from '@/widgets/decisions-feed'
import { ModelsTable } from '@/widgets/models-table'
import { UiSidePanel } from '@/shared/ui'

/** Страница «Решения»: лента решений и таблица моделей. Подробности решения открываются боковой панелью поверх страницы; выбор хранится в адресе (?decision=НОМЕР), повторный клик закрывает. */
const route = useRoute()
const router = useRouter()
const trading = useTradingStore()
const selected = computed(() => {
  const n = Number(route.query.decision)
  return typeof route.query.decision === 'string' && Number.isInteger(n) ? n : null
})
const decision = computed(() => trading.decisions?.items.find((d) => d.seq === selected.value) ?? null)
function pick(seq: number): void {
  void router.replace({ query: selected.value === seq ? {} : { decision: String(seq) } })
}
function close(): void {
  void router.replace({ query: {} })
}
</script>

<template>
  <main class="page">
    <DecisionsFeed :selected="selected" @select="pick" />
    <ModelsTable />
    <UiSidePanel :open="!!decision" label="Решение" @close="close">
      <DecisionDetailPanel v-if="decision" :key="decision.seq" :decision="decision" @close="close" />
    </UiSidePanel>
  </main>
</template>

<style scoped>
.page { max-width: 1180px; margin: 0 auto; padding: var(--space-5); display: grid; gap: var(--space-4); }
</style>
