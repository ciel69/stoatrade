<script setup lang="ts">
/** Логотип проекта: кольцо с разрывом и линия роста внутри. Тот же знак, что и favicon (public/favicon.svg), цвет берёт у родителя (акцент). */
withDefaults(defineProps<{ size?: number }>(), { size: 28 })
</script>

<template>
  <svg class="logo" viewBox="0 0 32 32" :width="size" :height="size" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
    <path d="M26.2 11.5A11 11 0 1 0 27 16" />
    <path d="M10.5 20l4-4.5 3.5 2.8 6-7.3" />
  </svg>
</template>

<style scoped>
.logo { flex: none; color: var(--accent); }
</style>
