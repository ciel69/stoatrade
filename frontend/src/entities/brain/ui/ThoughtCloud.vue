<script setup lang="ts">
/**
 * Облако мысли над мозгом: скруглённая форма и два пузырька-хвоста, ведущие к мозгу. Реплики это настроение, а не отчёт (см. lib/mood.ts), поэтому облако
 * скрыто от скринридера. Появление: прозрачность и масштаб от 0.94 с точки у мозга (левый нижний угол), смена реплики: короткий уход и приход текста.
 * Всё через opacity и transform, быстрее 300 мс; при prefers-reduced-motion текст просто меняется.
 */
defineProps<{ text: string | null }>()
</script>

<template>
  <Transition name="cloud">
    <div v-if="text" class="cloud" aria-hidden="true">
      <Transition name="quip" mode="out-in"><span :key="text" class="q">{{ text }}</span></Transition>
    </div>
  </Transition>
</template>

<style scoped>
.cloud {
  position: relative;
  display: inline-block;
  max-width: 100%;
  box-sizing: border-box;
  padding: 7px var(--space-4);
  border-radius: 22px 22px 22px 8px;
  background: var(--panel2);
  border: 1px solid var(--line2);
  color: var(--text);
  font-size: 13px;
  line-height: 1.3;
  transform-origin: 0 100%;
}
/* пузырьки-хвост к мозгу: большой и поменьше, по диагонали вниз и влево */
.cloud::before, .cloud::after { content: ''; position: absolute; border-radius: 50%; background: var(--panel2); border: 1px solid var(--line2); }
.cloud::before { width: 9px; height: 9px; left: -7px; bottom: -9px; }
.cloud::after { width: 5px; height: 5px; left: -14px; bottom: -16px; }
.q { display: inline-block; }

.cloud-enter-active { transition: opacity var(--dur-enter) var(--ease-out), transform var(--dur-enter) var(--ease-out); }
.cloud-leave-active { transition: opacity 160ms var(--ease-out), transform 160ms var(--ease-out); }
.cloud-enter-from, .cloud-leave-to { opacity: 0; transform: scale(0.94); }
.quip-enter-active { transition: opacity var(--dur-enter) var(--ease-out), transform var(--dur-enter) var(--ease-out); }
.quip-leave-active { transition: opacity 120ms var(--ease-out), transform 120ms var(--ease-out); }
.quip-enter-from { opacity: 0; transform: translateY(4px); }
.quip-leave-to { opacity: 0; transform: translateY(-4px); }
@media (prefers-reduced-motion: reduce) {
  .cloud-enter-active, .cloud-leave-active, .quip-enter-active, .quip-leave-active { transition: none; }
  .cloud-enter-from, .cloud-leave-to, .quip-enter-from, .quip-leave-to { transform: none; }
}
</style>
