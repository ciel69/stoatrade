<script setup lang="ts">
/** Надпись «Stoa» из локапа знака (docs/brand/stoa/concept-2b-lockup.svg, без сдвига translate(42 2)): своя геометрическая монолиния контурами SVG, шрифт не нужен.
 * Метрики те же, что у прежней надписи: базовая линия 24, строчные с 13, прописная и t с 6–7. Высота задаётся пропсом, ширина по пропорции; цвет у родителя (currentColor). */
const props = withDefaults(defineProps<{ height?: number }>(), { height: 16 })
const W = 56
const H = 28
</script>

<template>
  <svg class="wordmark" :viewBox="`0 4 ${W} ${H}`" :height="props.height" :width="(props.height * W) / H" role="img" aria-label="Stoa"
       fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round">
    <path d="M9.697 8.25A4.5 4.5 0 1 0 5.8 15A4.5 4.5 0 1 1 1.903 21.75" />
    <path d="M16.8 7V20.5A3.5 3.5 0 0 0 20.3 24M13.8 13H21.3" />
    <circle cx="31.6" cy="18.5" r="5.5" />
    <circle cx="48.4" cy="18.5" r="5.5" /><path d="M53.9 13V24" />
  </svg>
</template>

<style scoped>
.wordmark { flex: none; display: block; overflow: visible; }
</style>
