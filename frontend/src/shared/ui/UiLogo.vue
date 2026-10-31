<script setup lang="ts">
/** Логотип проекта, концепция «Проём» (docs/brand/logo): буква O разрезана пополам, правая половина сдвинута вверх-вправо — открытость (Open) и пробой уровня (Trader).
 * Тот же знак, что и favicon (public/favicon.svg), цвет берёт у родителя (акцент). */
withDefaults(defineProps<{ size?: number }>(), { size: 28 })
</script>

<template>
  <svg class="logo" viewBox="0 0 32 32" :width="size" :height="size" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round">
    <path d="M13.5 10A8.5 8.5 0 0 0 13.5 27" />
    <path d="M18.5 5A8.5 8.5 0 0 1 18.5 22" />
  </svg>
</template>

<style scoped>
.logo { flex: none; color: var(--accent); }
</style>
