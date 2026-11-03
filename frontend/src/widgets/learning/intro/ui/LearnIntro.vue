<script setup lang="ts">
import { LEARN_HELP_FOOT, LEARN_HELP_ITEMS, LEARN_HELP_TITLE } from '@/shared/lib'

/** Короткая справка под вердиктом: что значат n, интервал и «данных мало». Свёрнута по умолчанию (штатный элемент details: клавиатура и читалки работают без кода). */
</script>

<template>
  <details class="intro">
    <summary class="sum">
      <svg class="chev" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false"><path d="M5.5 3.5L10 8l-4.5 4.5" /></svg>
      <span>{{ LEARN_HELP_TITLE }}</span>
    </summary>
    <dl class="items">
      <div v-for="it in LEARN_HELP_ITEMS" :key="it.term" class="item">
        <dt>{{ it.term }}</dt>
        <dd>{{ it.text }}</dd>
      </div>
    </dl>
    <p class="foot">{{ LEARN_HELP_FOOT }}</p>
  </details>
</template>

<style scoped>
.intro { min-width: 0; padding: 0 var(--space-1); }
.sum { display: inline-flex; align-items: center; gap: var(--space-2); padding: var(--space-1) var(--space-2) var(--space-1) 0; border-radius: var(--radius-sm); font-size: 13px; color: var(--muted); cursor: pointer; list-style: none; user-select: none; transition: color var(--dur-fast) ease; }
.sum::-webkit-details-marker { display: none; }
.sum:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.chev { flex: none; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; transition: transform var(--dur-fast) var(--ease-out); }
.intro[open] .chev { transform: rotate(90deg); }
.intro[open] .sum { color: var(--text); }
.items { margin: var(--space-2) 0 0; display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: var(--space-3) var(--space-5); max-width: 1000px; }
.item { display: grid; gap: 2px; align-content: start; min-width: 0; }
.item dt { font-size: 13px; font-weight: 700; }
.item dd { margin: 0; font-size: 13px; line-height: 1.5; color: var(--muted); text-wrap: pretty; }
.foot { margin: var(--space-3) 0 0; font-size: 12px; color: var(--muted); }
@media (hover: hover) and (pointer: fine) {
  .sum:hover { color: var(--text); }
}
@media (prefers-reduced-motion: reduce) {
  .chev { transition: none; }
}
</style>
