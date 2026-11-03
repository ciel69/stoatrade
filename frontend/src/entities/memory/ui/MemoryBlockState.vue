<script setup lang="ts">
import { computed } from 'vue'
import { stateText } from '../lib/hints'
import type { BlockState } from '../model/types'

/**
 * Причина, по которой в блоке «Памяти» нет данных: загрузка (скелет), нет связи, база восстанавливается, нет данных, ошибка чтения.
 * Блок с ошибкой не ломает страницу: остальные блоки работают сами по себе. rows: сколько строк скелета (под высоту будущих данных).
 */
const props = withDefaults(defineProps<{ state: BlockState; reason?: string | null; what: string; rows?: number }>(), { reason: null, rows: 3 })
const text = computed(() => stateText(props.state, props.reason, props.what))
const tone = computed(() => (props.state === 'error' ? 'bad' : props.state === 'restoring' || props.state === 'link' ? 'warn' : 'calm'))
</script>

<template>
  <div v-if="state === 'loading'" class="mbs-skel" role="status" aria-live="polite">
    <span class="mbs-sr">{{ text }}</span>
    <span v-for="i in rows" :key="i" class="mbs-line" :style="{ width: `${88 - ((i * 17) % 40)}%` }" aria-hidden="true" />
  </div>
  <p v-else class="mbs" :class="tone" role="status">
    <svg v-if="tone !== 'calm'" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path v-if="tone === 'bad'" d="M12 4l9 16H3zM12 10v4M12 17.2v.01" />
      <path v-else d="M12 3a9 9 0 1 0 9 9M12 7v5l3 2" />
    </svg>
    <span>{{ text }}</span>
  </p>
</template>

<style scoped>
.mbs { margin: 0; display: grid; grid-template-columns: auto minmax(0, 1fr); align-items: start; gap: var(--space-2); font-size: 13px; line-height: 1.5; color: var(--muted); max-width: 70ch; text-wrap: pretty; }
.mbs.calm { grid-template-columns: minmax(0, 1fr); }
.mbs svg { margin-top: 2px; }
.mbs.bad svg { color: var(--neg); }
.mbs.warn svg { color: var(--warn); }
.mbs-sr { position: absolute; width: 1px; height: 1px; margin: -1px; padding: 0; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; border: 0; }
.mbs-skel { display: grid; gap: var(--space-3); padding: var(--space-1) 0; }
.mbs-line { display: block; height: 10px; border-radius: 99px; background: var(--line); }
@media (prefers-reduced-motion: no-preference) {
  .mbs-line { animation: mbs-pulse 1.4s var(--ease-in-out) infinite alternate; }
}
@keyframes mbs-pulse {
  from { opacity: 1; }
  to { opacity: 0.5; }
}
</style>
