<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute, useRouter, type LocationQueryRaw } from 'vue-router'
import { useRoleStore } from '@/entities/role'
import { parseUsagePanel, usagePanelQuery, type UsageBy, type UsagePeriod } from '@/entities/usage'
import { ConnectBoard } from '@/widgets/settings/connect-board'
import { ConnectPanel } from '@/widgets/settings/connect-panel'
import { RolePanel } from '@/widgets/settings/role-panel'
import { RolesBoard } from '@/widgets/settings/roles-board'
import { SwapsPanel } from '@/widgets/settings/swaps-panel'
import { UsageBoard } from '@/widgets/settings/usage-board'
import { UsagePanel } from '@/widgets/settings/usage-panel'
import { UiSidePanel } from '@/shared/ui'

/**
 * Страница «Система · Настройки»: организована по задачам, а не по терминам конфигурации. Блоки по порядку: «Кто думает» (роли мозга и их модели), «Что подключено» (провайдеры LLM, биржи),
 * «Сколько стоит» (расход LLM, история назначений). Только чтение; изменения появятся после входа по токену.
 * Одна боковая панель поверх страницы; что в ней, задаёт адрес и ровно один вид параметров: ?role=, ?provider=, ?exchange=, ?panel=swaps или ?usage=ID&days=&by= (строка расхода с периодом и разрезом). Повторный клик закрывает.
 */
const route = useRoute()
const router = useRouter()
onMounted(() => useRoleStore().start())
const q = (k: string): string | null => (typeof route.query[k] === 'string' && route.query[k] ? (route.query[k] as string) : null)
const role = computed(() => q('role'))
const provider = computed(() => q('provider'))
const exchange = computed(() => q('exchange'))
const swaps = computed(() => q('panel') === 'swaps')
const usage = computed(() => parseUsagePanel(route.query))
const open = computed(() => !!(role.value || provider.value || exchange.value || swaps.value || usage.value))
const label = computed(() => (role.value ? 'Роль мозга' : provider.value ? 'Провайдер LLM' : exchange.value ? 'Биржа' : usage.value ? 'Расход группы' : 'История назначений'))
/** Переключает параметр: то же значение закрывает панель, иначе все параметры заменяются одним. */
function toggle(query: LocationQueryRaw, same: boolean): void {
  void router.replace({ query: same ? {} : query })
}
const pickRole = (r: string): void => toggle({ role: r }, role.value === r)
const pickProvider = (id: string): void => toggle({ provider: id }, provider.value === id)
const pickExchange = (id: string): void => toggle({ exchange: id }, exchange.value === id)
const pickUsage = (id: string, days: UsagePeriod, by: UsageBy): void => {
  const u = usage.value
  toggle(usagePanelQuery(id, days, by), !!u && u.id === id && u.days === days && u.by === by)
}
const pickSwaps = (): void => toggle({ panel: 'swaps' }, swaps.value)
function close(): void {
  void router.replace({ query: {} })
}
</script>

<template>
  <main class="page">
    <div class="w-roles"><RolesBoard :selected="role" @select="pickRole" /></div>
    <div class="w-conn"><ConnectBoard :provider="provider" :exchange="exchange" @provider="pickProvider" @exchange="pickExchange" /></div>
    <div class="w-cost"><UsageBoard :swaps-open="swaps" :selected="usage" @swaps="pickSwaps" @select="pickUsage" @close="close" /></div>
    <UiSidePanel :open="open" :label="label" @close="close">
      <RolePanel v-if="role" :key="'r' + role" :role="role" @close="close" />
      <ConnectPanel v-else-if="provider" :key="'p' + provider" kind="provider" :id="provider" @close="close" />
      <ConnectPanel v-else-if="exchange" :key="'e' + exchange" kind="exchange" :id="exchange" @close="close" />
      <UsagePanel v-else-if="usage" :key="`u${usage.id}|${usage.days}|${usage.by}`" :id="usage.id" :days="usage.days" :by="usage.by" @close="close" />
      <SwapsPanel v-else-if="swaps" @close="close" />
    </UiSidePanel>
  </main>
</template>

<style scoped>
.page { gap: var(--space-6); }
.w-roles, .w-conn, .w-cost { min-width: 0; }
/* от 1500 px две колонки: «Кто думает» слева на всю высоту, «Что подключено» и «Сколько стоит» справа друг под другом */
@media (min-width: 1500px) {
  .page { grid-template-columns: minmax(0, 1.25fr) minmax(0, 1fr); column-gap: var(--space-6); row-gap: var(--space-6); align-items: start; }
  .w-roles { grid-column: 1; grid-row: 1 / span 2; }
  .w-conn { grid-column: 2; grid-row: 1; }
  .w-cost { grid-column: 2; grid-row: 2; }
}
</style>
