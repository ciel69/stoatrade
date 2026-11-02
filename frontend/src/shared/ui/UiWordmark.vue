<script setup lang="ts">
/** Надпись «Stoa» (docs/brand/stoa/concept-2b-lockup.svg): своя геометрическая монолиния контурами SVG, шрифт не нужен.
 * Метрики: базовая линия 24, строчные с 13, прописная S с 6, t с 7. У o и a радиус 5.75 вместо 5.5: свисание 0.25 ед. за строчные и базу,
 * иначе круглые выглядят мельче плоских. Просветы краска-краска по классам сторон: S–t 1.6, t–o 1.6, o–a 2.2 ед. (у пары круглых самый узкий).
 * `unit` — пикселей на единицу координат. viewBox начинается от краски: левый край S и верх прописной — это (0, 0) в пикселях.
 * `tagline` — подпись под словом живым текстом (выделяется, читается экранным чтецом), `taglinePx` — кегль в px,
 * `taglineBase` — её базовая линия в единицах слова. Левый край подписи по краю S (минус ~0.6 px собственного поля строчной a). Цвет подписи — var(--muted). */
const props = withDefaults(defineProps<{ unit?: number; stroke?: number; tagline?: string; taglinePx?: number; taglineBase?: number }>(),
  { unit: 0.875, stroke: 3, tagline: '', taglinePx: 11, taglineBase: 40.3 })
const X0 = 1.3        // левый край S по оси штриха (центр чаш 5.8, радиус 4.5)
const X1 = 55.2       // ножка a
const Y0 = 6          // верх S
const Y1 = 24.25      // низ o и a со свисанием
const DESC = 0.3      // запас под выносной g подписи, в долях кегля
const box = (): { x: number; y: number; w: number; h: number } => {
  const h = props.stroke / 2
  const x = X0 - h, y = Y0 - h
  const bottom = props.tagline ? props.taglineBase + (props.taglinePx * DESC) / props.unit : Y1 + h
  return { x, y, w: X1 + h - x, h: bottom - y }
}
const label = (): string => (props.tagline ? `Stoa, ${props.tagline}` : 'Stoa')
</script>

<template>
  <svg class="wordmark" :viewBox="`${box().x} ${box().y} ${box().w} ${box().h}`" :width="box().w * props.unit" :height="box().h * props.unit" role="img" :aria-label="label()"
       fill="none" stroke="currentColor" :stroke-width="props.stroke" stroke-linecap="round" stroke-linejoin="round">
    <path d="M9.697 8.25A4.5 4.5 0 1 0 5.8 15A4.5 4.5 0 1 1 1.903 21.75" />
    <path d="M17.9 7V20.5A3.5 3.5 0 0 0 21.4 24M14.9 13H22.4" />
    <circle cx="32.75" cy="18.5" r="5.75" />
    <circle cx="49.45" cy="18.5" r="5.75" /><path d="M55.2 13V24" />
    <text v-if="props.tagline" class="tagline" :x="box().x - 0.6 / props.unit" :y="props.taglineBase" :font-size="props.taglinePx / props.unit" stroke="none" aria-hidden="true">{{ props.tagline }}</text>
  </svg>
</template>

<style scoped>
.wordmark { flex: none; display: block; overflow: visible; }
.tagline { fill: var(--muted); font-family: var(--font); font-weight: 500; letter-spacing: 0.01em; }
</style>
