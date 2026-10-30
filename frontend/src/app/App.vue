<script setup lang="ts">
import { AppHeader } from '@/widgets/app-header'
import { EventCloud } from '@/widgets/event-cloud'
</script>

<template>
  <AppHeader />
  <RouterView />
  <EventCloud />
</template>
