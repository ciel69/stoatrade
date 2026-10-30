<script setup lang="ts">
import { AppHeader } from '@/widgets/app-header'
import { AppNav } from '@/widgets/app-nav'
import { EventCloud } from '@/widgets/event-cloud'
import { StartDayButton } from '@/widgets/start-day'
</script>

<template>
  <div class="shell">
    <AppNav />
    <div class="col">
      <AppHeader>
        <template #actions><StartDayButton /></template>
      </AppHeader>
      <RouterView />
    </div>
  </div>
  <EventCloud />
</template>

<style scoped>
.shell { display: grid; grid-template-columns: auto minmax(0, 1fr); min-height: 100vh; }
.col { min-width: 0; }
@media (max-width: 720px) {
  .shell { grid-template-columns: minmax(0, 1fr); }
}
</style>
