<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute, useRouter, type LocationQueryRaw } from 'vue-router'
import { useRoleStore } from '@/entities/role'
import { ConnectBoard } from '@/widgets/settings/connect-board'
import { ConnectPanel } from '@/widgets/settings/connect-panel'
import { RolePanel } from '@/widgets/settings/role-panel'
import { RolesBoard } from '@/widgets/settings/roles-board'
import { SwapsPanel } from '@/widgets/settings/swaps-panel'
import { UsageBoard } from '@/widgets/settings/usage-board'
import { UiSidePanel } from '@/shared/ui'

/**
 * Страница «Система · Настройки»: организована по задачам, а не по терминам конфигурации. Блоки по порядку: «Кто думает» (роли мозга и их модели), «Что подключено» (провайдеры LLM, биржи),
 * «Сколько стоит» (расход LLM, история назначений). Только чтение; изменения появятся после входа по токену.
 * Одна боковая панель поверх страницы; что в ней, задаёт адрес и ровно один параметр: ?role=, ?provider=, ?exchange= или ?panel=swaps. Повторный клик закрывает.
 */
const route = useRoute()
const router = useRouter()
onMounted(() => useRoleStore().start())
const q = (k: string): string | null => (typeof route.query[k] === 'string' && route.query[k] ? (route.query[k] as string) : null)
const role = computed(() => q('role'))
const provider = computed(() => q('provider'))
const exchange = computed(() => q('exchange'))
const swaps = computed(() => q('panel') === 'swaps')
const open = computed(() => !!(role.value || provider.value || exchange.value || swaps.value))
const label = computed(() => (role.value ? 'Роль мозга' : provider.value ? 'Провайдер LLM' : exchange.value ? 'Биржа' : 'История назначений'))
/** Переключает параметр: то же значение закрывает панель, иначе все параметры заменяются одним. */
function toggle(query: LocationQueryRaw, same: boolean): void {
  void router.replace({ query: same ? {} : query })
}
const pickRole = (r: string): void => toggle({ role: r }, role.value === r)
const pickProvider = (id: string): void => toggle({ provider: id }, provider.value === id)
const pickExchange = (id: string): void => toggle({ exchange: id }, exchange.value === id)
const pickSwaps = (): void => toggle({ panel: 'swaps' }, swaps.value)
function close(): void {
  void router.replace({ query: {} })
}
</script>

<template>
  <main class="page">
    <RolesBoard :selected="role" @select="pickRole" />
    <ConnectBoard :provider="provider" :exchange="exchange" @provider="pickProvider" @exchange="pickExchange" />
    <UsageBoard :swaps-open="swaps" @swaps="pickSwaps" />
    <UiSidePanel :open="open" :label="label" @close="close">
      <RolePanel v-if="role" :key="'r' + role" :role="role" @close="close" />
      <ConnectPanel v-else-if="provider" :key="'p' + provider" kind="provider" :id="provider" @close="close" />
      <ConnectPanel v-else-if="exchange" :key="'e' + exchange" kind="exchange" :id="exchange" @close="close" />
      <SwapsPanel v-else-if="swaps" @close="close" />
    </UiSidePanel>
  </main>
</template>

<style scoped>
.page { max-width: 1180px; margin: 0 auto; padding: var(--space-5); display: grid; gap: var(--space-6, 32px); }
</style>
