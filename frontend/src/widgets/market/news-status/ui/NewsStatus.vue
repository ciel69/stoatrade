<script setup lang="ts">
import { computed } from 'vue'
import { newsVerdict, useNewsStore } from '@/entities/headline'
import { UiStatusDot } from '@/shared/ui'
import { useNow } from '@/shared/lib'

/**
 * Одна фраза-вывод над лентой: идут ли новости нормально, не отвечает ли источник, выключен ли читатель, не идёт ли окно блокировки. Подробности (читатель, источники, календарь)
 * открывает страница боковой панелью по кнопке «Подробности». Данные из темы news по WebSocket.
 */
defineProps<{ open?: boolean }>()
const emit = defineEmits<{ details: [] }>()
const store = useNewsStore()
const now = useNow()
const verdict = computed(() => (store.snapshot ? newsVerdict(store.snapshot, now.value) : null))
</script>

<template>
  <div class="bar" role="status" :class="verdict ? `t-${verdict.tone}` : ''">
    <template v-if="verdict">
      <UiStatusDot :state="verdict.tone" :label="verdict.tone === 'ok' ? 'В порядке' : verdict.tone === 'warn' ? 'Есть что проверить' : 'Справка'" />
      <p class="text">{{ verdict.text }}</p>
      <button type="button" class="more" :aria-expanded="!!open" @click="emit('details')">Подробности</button>
    </template>
    <p v-else class="text muted">Загружаем состояние новостей…</p>
  </div>
</template>

<style scoped>
.bar { display: flex; align-items: center; gap: var(--space-3); min-height: 44px; padding: var(--space-2) var(--space-4); border: 1px solid var(--line); border-radius: var(--radius); background: var(--panel); min-width: 0; }
.text { flex: 1 1 auto; min-width: 0; margin: 0; font-size: 14px; line-height: 1.4; overflow-wrap: anywhere; }
.muted { color: var(--muted); }
.more { flex: none; min-height: 36px; padding: 0 var(--space-3); font: inherit; font-size: 13px; color: var(--text); background: transparent; border: 1px solid var(--line2); border-radius: var(--radius-sm); cursor: pointer; transition: color var(--dur-fast) ease, border-color var(--dur-fast) ease, transform 120ms var(--ease-out); }
.more:active { transform: scale(0.97); }
.more:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
@media (hover: hover) and (pointer: fine) {
  .more:hover { border-color: var(--muted); }
}
@media (prefers-reduced-motion: reduce) {
  .more { transition: color var(--dur-fast) ease, border-color var(--dur-fast) ease; }
  .more:active { transform: none; }
}
@media (max-width: 520px) {
  .bar { flex-wrap: wrap; }
  .text { flex-basis: calc(100% - 28px); }
}
</style>
