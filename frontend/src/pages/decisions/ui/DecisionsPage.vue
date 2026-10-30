<script setup lang="ts">
import { DecisionsFeed } from '@/widgets/decisions-feed'
import { ModelsTable } from '@/widgets/models-table'
</script>

<template>
  <main class="page">
    <DecisionsFeed />
    <ModelsTable />
  </main>
</template>

<style scoped>
.page { max-width: 1180px; margin: 0 auto; padding: var(--space-5); display: grid; gap: var(--space-4); }
</style>
