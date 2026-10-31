<script setup lang="ts">
import { onMounted, ref } from 'vue'

/**
 * Рамка содержимого боковой панели (UiSidePanel): заголовок слева (слот title), крестик справа, прокручиваемое тело (слот по умолчанию). Фокус при открытии на рамке,
 * чтобы Esc и Tab работали сразу; закрытие по Esc обрабатывает сама панель.
 */
defineProps<{ label: string }>()
const emit = defineEmits<{ close: [] }>()
const root = ref<HTMLElement | null>(null)
onMounted(() => root.value?.focus({ preventScroll: true }))
</script>

<template>
  <aside ref="root" class="panel" tabindex="-1" :aria-label="label">
    <header class="head">
      <div class="ttl"><slot name="title" /></div>
      <button type="button" class="x" aria-label="Закрыть" @click="emit('close')"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg></button>
    </header>
    <div class="body"><slot /></div>
  </aside>
</template>

<style scoped>
.panel { display: flex; flex-direction: column; min-height: 0; outline: none; }
.head { display: flex; align-items: center; gap: var(--space-3); padding: var(--space-4) var(--space-4) var(--space-3); border-bottom: 1px solid var(--line); }
.ttl { flex: 1 1 auto; min-width: 0; display: flex; align-items: baseline; flex-wrap: wrap; gap: var(--space-2) var(--space-3); }
.x { flex: none; display: grid; place-items: center; width: 32px; height: 32px; border: 0; border-radius: var(--radius-sm); background: none; color: var(--muted); cursor: pointer; }
.x:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.body { flex: 1 1 auto; min-height: 0; overflow-y: auto; padding: var(--space-4); display: grid; gap: var(--space-4); align-content: start; }
@media (hover: hover) and (pointer: fine) {
  .x:hover { color: var(--text); background: var(--panel2); }
}
</style>
