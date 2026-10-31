<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useRoleStore } from '@/entities/role'
import { RolePanel } from '@/widgets/settings/role-panel'
import { RolesBoard } from '@/widgets/settings/roles-board'
import { UiSidePanel } from '@/shared/ui'

/**
 * Страница «Система · Настройки»: организована по задачам, а не по терминам конфигурации. Сейчас блок «Кто думает» (роли мозга и их модели); «Что подключено» (провайдеры, ключи, биржи)
 * и «Сколько стоит» (расход LLM, история изменений) добавим следующими блоками. Только чтение; изменения появятся после входа по токену. Выбранная роль в адресе (?role=), панель справа поверх страницы.
 */
const route = useRoute()
const router = useRouter()
onMounted(() => useRoleStore().start())
const role = computed(() => (typeof route.query.role === 'string' && route.query.role ? route.query.role : null))
function pick(r: string): void {
  void router.replace({ query: role.value === r ? {} : { role: r } })
}
function close(): void {
  void router.replace({ query: {} })
}
</script>

<template>
  <main class="page">
    <RolesBoard :selected="role" @select="pick" />
    <UiSidePanel :open="!!role" label="Роль мозга" @close="close">
      <RolePanel v-if="role" :key="role" :role="role" @close="close" />
    </UiSidePanel>
  </main>
</template>

<style scoped>
.page { max-width: 1180px; margin: 0 auto; padding: var(--space-5); display: grid; gap: var(--space-5); }
</style>
