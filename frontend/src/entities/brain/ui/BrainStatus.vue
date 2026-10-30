<script setup lang="ts">
import { computed } from 'vue'
import { UiStatusDot, type DotState } from '@/shared/ui'
import type { BrainStatus } from '../model/types'

const props = defineProps<{ status: BrainStatus; text: string }>()
const dot = computed<DotState>(() => ({ down: 'bad', protect: 'warn', thinking: 'busy', idle: 'ok' } as const)[props.status])
</script>

<template>
  <span class="status"><UiStatusDot :state="dot" :label="text" /><span class="text">{{ text }}</span></span>
</template>

<style scoped>
.status { display: inline-flex; align-items: center; gap: var(--space-2); font-weight: 600; }
.text { font-size: 14px; }
</style>
