<script setup lang="ts">
import { computed } from 'vue'
import { DASH, ageSince } from '@/entities/cmkt'
import { useNow } from '@/shared/lib'

/** Возраст по метке `ts` в секундах: тикает только этот узел, а не весь экран. */
const props = defineProps<{ ts: number | null; label?: string }>()
const now = useNow()
const text = computed(() => (props.ts == null ? DASH : ageSince(props.ts, now.value)))
</script>

<template>
  <span class="age">{{ label ?? 'снимок' }} {{ text }}</span>
</template>

<style scoped>
.age { font-size: 12px; color: var(--muted); white-space: nowrap; font-variant-numeric: tabular-nums; }
</style>
