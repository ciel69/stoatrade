<script setup lang="ts">
/** Надпись «Open Trader» из локапа логотипа (docs/brand/logo/concept-1-lockup.svg): своя геометрическая монолиния контурами SVG, шрифт не нужен.
 * Высота задаётся пропсом, ширина по пропорции; цвет у родителя (currentColor). */
const props = withDefaults(defineProps<{ height?: number }>(), { height: 16 })
const W = 171
const H = 28
</script>

<template>
  <svg class="wordmark" :viewBox="`0 4 ${W} ${H}`" :height="props.height" :width="(props.height * W) / H" role="img" aria-label="Open Trader"
       fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round">
    <circle cx="10.3" cy="15" r="9" />
    <path d="M25.1 13V30" /><circle cx="30.6" cy="18.5" r="5.5" />
    <path d="M41.9 18.5H52.9A5.5 5.5 0 1 0 51.61 22.04" />
    <path d="M58.7 13V24M58.7 18.5A5.5 5.5 0 0 1 69.7 18.5V24" />
    <path d="M83.7 6H97.7M90.7 6V24" />
    <path d="M102.2 13V24M102.2 18.5A5.5 5.5 0 0 1 107.7 13" />
    <circle cx="118.2" cy="18.5" r="5.5" /><path d="M123.7 13V24" />
    <circle cx="135" cy="18.5" r="5.5" /><path d="M140.5 6V24" />
    <path d="M146.3 18.5H157.3A5.5 5.5 0 1 0 156.01 22.04" />
    <path d="M163.1 13V24M163.1 18.5A5.5 5.5 0 0 1 168.6 13" />
  </svg>
</template>

<style scoped>
.wordmark { flex: none; display: block; overflow: visible; }
</style>
