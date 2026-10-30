<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { PlanDetailPanel } from '@/widgets/plan-detail'
import { PlansList } from '@/widgets/plans-list'

/**
 * Страница «Планы»: список всех планов и справа панель выбранного. Выбор хранится в адресе (?plan=ID): ссылку на план можно открыть и отправить; Esc, крестик и повторный выбор закрывают панель.
 * Широкий экран: панель стоит рядом со списком и остаётся на месте при прокрутке. Узкий: панель поверх страницы на весь экран.
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
  <main class="page" :class="{ open: selected }">
    <PlansList :selected="selected" @select="pick" />
    <Transition name="pd">
      <div v-if="selected" :key="selected" class="side">
        <PlanDetailPanel :plan-id="selected" @close="close" />
      </div>
    </Transition>
    <div v-if="selected" class="scrim" @click="close" />
  </main>
</template>

<style scoped>
.page { max-width: 1180px; margin: 0 auto; padding: var(--space-5); display: grid; gap: var(--space-4); align-items: start; }
.page.open { grid-template-columns: minmax(0, 1fr) minmax(360px, 460px); }
.side { position: sticky; top: var(--space-4); height: calc(100vh - var(--space-4) * 2); display: flex; flex-direction: column; min-height: 0; }
.side > :deep(.panel) { flex: 1 1 auto; }
.scrim { display: none; }
@media (max-width: 1100px) {
  .page.open { grid-template-columns: minmax(0, 1fr); }
  .side { position: fixed; z-index: 45; inset: var(--space-3); height: auto; }
  .scrim { display: block; position: fixed; z-index: 44; inset: 0; background: rgba(0, 0, 0, 0.5); }
}
</style>

<style>
/* панель плана выезжает справа: редкое действие, 220 мс, сильное замедление на выходе; закрытие быстрее и без сдвига */
.pd-enter-active { transition: opacity 220ms var(--ease-out), transform 220ms var(--ease-out); }
.pd-leave-active { transition: opacity 120ms var(--ease-out); }
.pd-enter-from { opacity: 0; transform: translateX(24px); }
.pd-leave-to { opacity: 0; }
@media (prefers-reduced-motion: reduce) {
  .pd-enter-from { transform: none; }
}
</style>
