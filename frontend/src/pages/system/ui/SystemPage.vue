<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useControlStore } from '@/entities/control'
import { useSystemStore } from '@/entities/system'
import { PlatformHero } from '@/widgets/system/platform-hero'
import { RiskLevel } from '@/widgets/system/risk-level'
import { SystemLimits } from '@/widgets/system/system-limits'
import { SystemMore } from '@/widgets/system/system-more'
import { SystemNodes } from '@/widgets/system/system-nodes'
import { SystemPanel } from '@/widgets/system/system-panel'
import { usePlatformStatus } from '@/widgets/system/platform-status'
import { UiSidePanel } from '@/shared/ui'

/**
 * Страница «Система · Управление»: сверху состояние платформы и главное действие, ниже узлы, сессия с запасами и капитал, в конце строки подробностей. Подробности открываются боковой
 * панелью поверх страницы (?panel=ready|exec|sources|history). Под главным действием стоит уровень риска (он относится к запуску платформы): выбор уровня и «Применить» через единое окно «Принимаю риски».
 */
const route = useRoute()
const router = useRouter()
/** Тот же вывод, что в индикаторе шапки: «могу ли я оставить платформу торговать». */
const { summary } = usePlatformStatus()
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
    <div class="w-hero"><PlatformHero :headline="summary.headline" /></div>
    <div class="w-risk"><RiskLevel /></div>
    <div class="w-nodes"><SystemNodes /></div>
    <div class="w-lim"><SystemLimits /></div>
    <div class="w-more"><SystemMore :selected="panel" @open="open" /></div>
    <UiSidePanel :open="!!panel" label="Подробности системы" @close="close">
      <SystemPanel v-if="panel" :key="panel" :kind="panel" @close="close" />
    </UiSidePanel>
  </main>
</template>

<style scoped>
.page { gap: var(--space-5); }
.w-hero, .w-risk, .w-nodes, .w-lim, .w-more { min-width: 0; }
/* от 1500 px: состояние на всю ширину, под ним уровень риска, ниже слева узлы, справа подробности, под ними сессия, запас и капитал тремя колонками */
@media (min-width: 1500px) {
  .page { grid-template-columns: minmax(0, 3fr) minmax(0, 2fr); column-gap: var(--space-5); align-items: start; }
  .w-hero, .w-risk, .w-lim { grid-column: 1 / -1; }
  .w-risk { grid-row: 2; }
  .w-nodes { grid-column: 1; grid-row: 3; }
  .w-more { grid-column: 2; grid-row: 3; }
  .w-lim { grid-row: 4; }
}
</style>
