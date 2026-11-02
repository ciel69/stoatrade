<script setup lang="ts">
/** Знак Stoa, вариант 2b «Полукольца с хвостами» (docs/brand/stoa): две полудуги S с короткими горизонтальными концами.
 * Половины — мозг и исполнитель: роли переходят одна в другую, но не слиты; горизонтали намекают на уровни.
 * Тот же знак, что и favicon (public/favicon.svg), цвет берёт у родителя (акцент). */
withDefaults(defineProps<{ size?: number }>(), { size: 28 })
</script>

<template>
  <svg class="logo" viewBox="0 0 32 32" :width="size" :height="size" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round">
    <path d="M18 5H13A6 6 0 0 0 13 17" />
    <path d="M19 15A6 6 0 0 1 19 27H14" />
  </svg>
</template>

<style scoped>
.logo { flex: none; color: var(--accent); }
</style>
