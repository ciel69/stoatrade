<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useDayStore } from '@/entities/day'
import { sectionOf } from '@/shared/config'

/**
 * Полоса вкладок пункта меню под шапкой: у пунктов, которые объединяют несколько страниц (Рынок, Торговля, Мозг). Вкладка это обычная страница со своим адресом
 * (/trading/plans), поэтому ссылки и «назад» работают как у отдельных страниц; у остальных пунктов (Обзор) полосы нет.
 */
const route = useRoute()
const section = computed(() => sectionOf(route.path))
// метка с числом на вкладке, где что-то ждёт вашего ответа («День»)
const day = useDayStore()
onMounted(() => day.start())
const count = (key: string | undefined): number => (key === 'day' ? day.needs.count : 0)
</script>

<template>
  <nav v-if="section?.tabs && section.tabs.length > 1" class="tabs" :aria-label="section.label" :style="{ '--zc': `var(--zone-${section.zone})` }">
    <RouterLink v-for="t in section.tabs" :key="t.to" :to="t.to" class="tab" :class="{ on: route.path === t.to }" :aria-current="route.path === t.to ? 'page' : undefined">{{ t.label }}<span v-if="count(t.badge)" class="badge">{{ count(t.badge) }}<span class="sr">, ждёт вашего ответа</span></span></RouterLink>
  </nav>
</template>

<style scoped>
.tabs { display: flex; gap: var(--space-1); padding: 0 var(--space-5); border-bottom: 1px solid var(--line); overflow-x: auto; scrollbar-width: none; }
.tabs::-webkit-scrollbar { display: none; }
.tab { position: relative; padding: 11px var(--space-3); color: var(--muted); font-size: 13px; text-decoration: none; white-space: nowrap; transition: color var(--dur-fast) ease; }
.tab::after { content: ''; position: absolute; left: var(--space-3); right: var(--space-3); bottom: -1px; height: 2px; border-radius: 2px; background: var(--zc, var(--accent)); transform: scaleX(0); transform-origin: left; transition: transform 200ms var(--ease-out); }
.tab.on { color: var(--text); font-weight: 600; }
.badge { display: inline-block; margin-left: var(--space-2); min-width: 18px; height: 18px; padding: 0 5px; border-radius: 99px; background: var(--warn); color: var(--bg); font-size: 11px; font-weight: 700; line-height: 18px; text-align: center; vertical-align: 1px; font-variant-numeric: tabular-nums; }
.sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
.tab.on::after { transform: scaleX(1); }
.tab:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; border-radius: var(--radius-sm); }
@media (hover: hover) and (pointer: fine) {
  .tab:not(.on):hover { color: var(--text); }
}
@media (prefers-reduced-motion: reduce) {
  .tab::after { transition: none; }
}
</style>
