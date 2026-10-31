<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { PlanDetailPanel } from '@/widgets/plan-detail'
import { PlansList } from '@/widgets/plans-list'
import { UiSidePanel } from '@/shared/ui'

/**
 * Страница «Планы»: список всех планов, детали выбранного открываются боковой панелью поверх страницы (список не сдвигается). Выбор хранится в адресе (?plan=ID): ссылку на план
 * можно открыть и отправить; Esc, крестик и повторный выбор закрывают панель.
 */
const route = useRoute()
const router = useRouter()
const selected = computed(() => (typeof route.query.plan === 'string' && route.query.plan ? route.query.plan : null))

function pick(id: string): void {
  void router.replace({ query: selected.value === id ? {} : { plan: id } })
}
function close(): void {
  void router.replace({ query: {} })
}
</script>

<template>
  <main class="page">
    <PlansList :selected="selected" @select="pick" />
    <UiSidePanel :open="!!selected" label="План" @close="close">
      <PlanDetailPanel v-if="selected" :key="selected" :plan-id="selected" @close="close" />
    </UiSidePanel>
  </main>
</template>

<style scoped>
.page { max-width: 1180px; margin: 0 auto; padding: var(--space-5); display: grid; gap: var(--space-4); }
</style>
