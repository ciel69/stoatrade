<script setup lang="ts">
import { computed } from 'vue'
import { useLive } from '@/shared/api'
import { UiStatusDot, type DotState } from '@/shared/ui'

const { link } = useLive()
const dot = computed<DotState>(() => ({ open: 'ok', connecting: 'warn', closed: 'bad' } as const)[link.value])
const text = computed(() => ({ open: 'связь есть', connecting: 'подключение…', closed: 'нет связи, переподключаюсь' } as const)[link.value])
</script>

<template>
  <header class="bar">
    <h1 class="logo">open-trader <span class="tag">новый дашборд</span></h1>
    <span class="link" role="status"><UiStatusDot :state="dot" :label="text" /><span>{{ text }}</span></span>
  </header>
</template>

<style scoped>
.bar { display: flex; align-items: center; justify-content: space-between; gap: var(--space-4); padding: var(--space-3) var(--space-5); border-bottom: 1px solid var(--line); }
.logo { margin: 0; font-size: 18px; letter-spacing: -0.02em; }
.tag { margin-left: var(--space-2); font-size: 12px; font-weight: 400; color: var(--muted); }
.link { display: inline-flex; align-items: center; gap: var(--space-2); font-size: 13px; color: var(--muted); }
</style>
