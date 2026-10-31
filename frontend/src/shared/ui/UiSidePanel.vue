<script setup lang="ts">
import { onMounted, onUnmounted } from 'vue'

/**
 * Боковая панель деталей: выезжает справа поверх страницы, контент под ней не сдвигается и не сжимается. Стоит выше меню и шапки, ниже модальных окон.
 * Широкий экран: страница под панелью остаётся живой (клик по другой строке меняет содержимое, по той же закрывает), затемнения нет. Узкий: затемнение, клик по нему закрывает.
 * Esc закрывает. Открытие 220 мс с сильным замедлением, закрытие быстрее и без сдвига; при reduce motion только прозрачность. Содержимое панели (заголовок, крестик, прокрутка) отдаёт слот.
 */
const props = defineProps<{ open: boolean; label: string }>()
const emit = defineEmits<{ close: [] }>()

function onKey(ev: KeyboardEvent): void {
  if (props.open && ev.key === 'Escape') emit('close')
}
onMounted(() => document.addEventListener('keydown', onKey))
onUnmounted(() => document.removeEventListener('keydown', onKey))
</script>

<template>
  <Teleport to="body">
    <Transition name="sp">
      <div v-if="open" class="sp">
        <div class="scrim" @click="emit('close')" />
        <div class="sheet" role="dialog" :aria-label="label"><slot /></div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.sp { position: fixed; inset: 0; z-index: 50; pointer-events: none; }
.scrim { display: none; position: absolute; inset: 0; background: rgba(0, 0, 0, 0.5); pointer-events: auto; }
.sheet { position: absolute; top: 0; right: 0; bottom: 0; width: min(460px, 100vw); display: flex; flex-direction: column; min-height: 0; pointer-events: auto; background: var(--panel); border-left: 1px solid var(--line); box-shadow: -16px 0 40px rgba(0, 0, 0, 0.35); }
.sheet > :deep(.panel) { flex: 1 1 auto; min-height: 0; border: 0; border-radius: 0; background: transparent; }
@media (max-width: 1100px) {
  .scrim { display: block; }
}
.sp-enter-active .sheet { transition: transform 220ms var(--ease-out); }
.sp-enter-active, .sp-enter-active .scrim { transition: opacity 220ms var(--ease-out); }
.sp-leave-active, .sp-leave-active .scrim { transition: opacity 120ms var(--ease-out); }
.sp-enter-from { opacity: 0; }
.sp-enter-from .sheet { transform: translateX(32px); }
.sp-leave-to { opacity: 0; }
@media (prefers-reduced-motion: reduce) {
  .sp-enter-from .sheet { transform: none; }
}
</style>
