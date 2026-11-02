<script setup lang="ts">
/** Знак Stoa, вариант 2b «Полукольца с хвостами» (docs/brand/stoa): две полудуги S с короткими горизонтальными концами.
 * Половины — мозг и исполнитель: роли переходят одна в другую, но не слиты; горизонтали намекают на уровни.
 * Хвосты 6.5 ед. (удлинены на 1.5 после разбора локапа: наклон силуэта 15° → 12°, ровнее рядом с прямым словом).
 * Тот же знак, что и favicon (public/favicon.svg), цвет берёт у родителя (акцент).
 * `stroke` — толщина в единицах 32×32; 3.5 как в файлах бренда, крупному знаку в шапке легче штрих, чтобы он не перевешивал слово. */
withDefaults(defineProps<{ size?: number; stroke?: number }>(), { size: 28, stroke: 3.5 })
</script>

<template>
  <svg class="logo" viewBox="0 0 32 32" :width="size" :height="size" aria-hidden="true" fill="none" stroke="currentColor" :stroke-width="stroke" stroke-linecap="round" stroke-linejoin="round">
    <path d="M19.5 5H13A6 6 0 0 0 13 17" />
    <path d="M19 15A6 6 0 0 1 19 27H12.5" />
  </svg>
</template>

<style scoped>
.logo { flex: none; color: var(--accent); }
</style>
