<script setup lang="ts">
import { BrainPanel } from '@/widgets/brain-panel'
</script>

<template>
  <main class="page">
    <BrainPanel />
  </main>
</template>

<style scoped>
.page { max-width: 1100px; margin: 0 auto; padding: var(--space-5); display: grid; gap: var(--space-4); }
</style>
