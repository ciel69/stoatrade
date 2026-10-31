<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useControlStore } from '@/entities/control'
import { useSystemStore } from '@/entities/system'
import { PlatformHero } from '@/widgets/system/platform-hero'
import { SystemLimits } from '@/widgets/system/system-limits'
import { SystemMore } from '@/widgets/system/system-more'
import { SystemNodes } from '@/widgets/system/system-nodes'
import { SystemPanel } from '@/widgets/system/system-panel'
import { UiSidePanel } from '@/shared/ui'

/**
 * Страница «Система · Управление»: сверху состояние платформы и главное действие, ниже узлы, сессия с запасами и капитал, в конце строки подробностей. Подробности открываются боковой
 * панелью поверх страницы (?panel=ready|exec|sources|history). Страница ничего не меняет в конфигурации: только безопасные запуск и остановка дня через общее окно.
 */
const route = useRoute()
const router = useRouter()
onMounted(() => { useSystemStore().start(); useControlStore().start() })
const KINDS = ['ready', 'exec', 'sources', 'history']
const panel = computed(() => (typeof route.query.panel === 'string' && KINDS.includes(route.query.panel) ? route.query.panel : null))
function open(kind: string): void {
  void router.replace({ query: panel.value === kind ? {} : { panel: kind } })
}
function close(): void {
  void router.replace({ query: {} })
}
</script>

<template>
  <main class="page">
    <PlatformHero />
    <SystemNodes />
    <SystemLimits />
    <SystemMore :selected="panel" @open="open" />
    <UiSidePanel :open="!!panel" label="Подробности системы" @close="close">
      <SystemPanel v-if="panel" :key="panel" :kind="panel" @close="close" />
    </UiSidePanel>
  </main>
</template>

<style scoped>
.page { max-width: 1180px; margin: 0 auto; padding: var(--space-5); display: grid; gap: var(--space-5); }
</style>
