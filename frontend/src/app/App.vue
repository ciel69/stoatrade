<script setup lang="ts">
import { ActionDialog } from '@/widgets/action-dialog'
import { AppHeader } from '@/widgets/app-header'
import { AppNav } from '@/widgets/app-nav'
import { EventCloud } from '@/widgets/event-cloud'
import { PlatformStatus } from '@/widgets/system/platform-status'
import { SectionTabs } from '@/widgets/section-tabs'
import { StartDayButton } from '@/widgets/start-day'
</script>

<template>
  <div class="shell">
    <AppNav>
      <template #action="{ collapsed }"><StartDayButton :compact="collapsed" /></template>
    </AppNav>
    <div class="col">
      <div class="top">
        <AppHeader>
          <template #status><PlatformStatus /></template>
        </AppHeader>
        <SectionTabs />
      </div>
      <RouterView />
    </div>
  </div>
  <EventCloud />
  <ActionDialog />
</template>

<style scoped>
.shell { display: grid; grid-template-columns: auto minmax(0, 1fr); min-height: 100vh; }
.col { min-width: 0; }
/* Шапка и табы держатся при прокрутке: на длинных страницах переключение раздела не требует возврата наверх.
   z-index ниже боковой колонки на телефоне (41) и её затемнения (40), чтобы открытое меню было сверху. */
.top { position: sticky; top: 0; z-index: 20; background: var(--bg); }
@media (max-width: 720px) {
  .shell { grid-template-columns: minmax(0, 1fr); }
}
</style>
