<script setup lang="ts">
/**
 * Строка изменения: «было → станет» одной фразой, последствия словами, «Сбросить» и «Применить». Само подтверждение рисков делает общий диалог, здесь только запуск.
 * Появляется и исчезает прозрачностью и сдвигом на несколько пикселей.
 */
defineProps<{ line: string; effect: string; apply: string; busy: boolean; hint?: string }>()
defineEmits<{ apply: []; reset: [] }>()
</script>

<template>
  <div class="bar" role="group" aria-label="Изменение">
    <p class="line"><span class="cap">Было → станет</span><b>{{ line }}</b></p>
    <p class="effect">{{ effect }}</p>
    <p v-if="hint" class="effect warn">{{ hint }}</p>
    <div class="acts">
      <button type="button" class="ghost" :disabled="busy" @click="$emit('reset')">Сбросить</button>
      <button type="button" class="cta" :disabled="busy" @click="$emit('apply')">{{ apply }}</button>
    </div>
  </div>
</template>

<style scoped>
.bar { display: grid; gap: var(--space-2); padding-top: var(--space-3); border-top: 1px solid var(--line); }
.line { margin: 0; display: grid; gap: 2px; font-size: 14px; }
.line b { font-weight: 650; overflow-wrap: anywhere; }
.cap { font-size: 12px; color: var(--muted); }
.effect { margin: 0; font-size: 12px; line-height: 1.5; color: var(--muted); }
.effect.warn { color: var(--warn); }
.acts { display: flex; justify-content: flex-end; gap: var(--space-2); }
.ghost, .cta { min-height: 40px; padding: 0 var(--space-4); font: inherit; font-size: 13px; border-radius: var(--radius-sm); cursor: pointer; white-space: nowrap; transition: color var(--dur-fast) ease, border-color var(--dur-fast) ease, opacity var(--dur-fast) ease, filter var(--dur-fast) ease, transform 120ms var(--ease-out); }
.ghost { background: transparent; color: var(--muted); border: 1px solid var(--line2); }
.cta { background: var(--accent); color: var(--bg); border: 0; font-weight: 650; }
.ghost:disabled, .cta:disabled { opacity: 0.45; cursor: not-allowed; }
.ghost:active:not(:disabled), .cta:active:not(:disabled) { transform: scale(0.97); }
.ghost:focus-visible, .cta:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
@media (hover: hover) and (pointer: fine) {
  .ghost:hover:not(:disabled) { border-color: var(--muted); color: var(--text); }
  .cta:hover:not(:disabled) { filter: brightness(1.08); }
}
@media (max-width: 520px) {
  .acts { flex-direction: column-reverse; }
}
@media (prefers-reduced-motion: reduce) {
  .ghost, .cta { transition: color var(--dur-fast) ease, border-color var(--dur-fast) ease, opacity var(--dur-fast) ease; }
  .ghost:active:not(:disabled), .cta:active:not(:disabled) { transform: none; }
}
</style>
