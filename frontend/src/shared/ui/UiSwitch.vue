<script setup lang="ts">
/**
 * Переключатель «включено или выключено». Подпись и пояснение связаны через aria-labelledby и aria-describedby (передаются id снаружи).
 * Ползунок едет transform, цвет дорожки меняется отдельно; при prefers-reduced-motion ползунок просто меняет цвет.
 */
defineProps<{ modelValue: boolean; labelId?: string; describedby?: string; label?: string; disabled?: boolean }>()
defineEmits<{ 'update:modelValue': [value: boolean] }>()
</script>

<template>
  <button
    type="button" role="switch" class="sw" :aria-checked="modelValue" :aria-labelledby="labelId" :aria-label="labelId ? undefined : label" :aria-describedby="describedby" :disabled="disabled"
    @click="$emit('update:modelValue', !modelValue)"
  />
</template>

<style scoped>
.sw { position: relative; flex: none; width: 44px; height: 26px; padding: 0; border: 1px solid var(--line2); border-radius: 13px; background: var(--panel2); cursor: pointer; transition: background-color var(--dur-fast) ease, border-color var(--dur-fast) ease, opacity var(--dur-fast) ease, transform 120ms var(--ease-out); }
.sw::after { content: ''; position: absolute; top: 2px; left: 2px; width: 20px; height: 20px; border-radius: 50%; background: var(--muted); transform: translateX(0); transition: transform var(--dur-enter) var(--ease-out), background-color var(--dur-fast) ease; }
.sw[aria-checked='true'] { background: var(--accent-bg); border-color: var(--accent); }
.sw[aria-checked='true']::after { background: var(--accent); transform: translateX(18px); }
.sw:disabled { opacity: 0.45; cursor: not-allowed; }
.sw:active:not(:disabled) { transform: scale(0.96); }
.sw:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
@media (hover: hover) and (pointer: fine) {
  .sw:hover:not(:disabled) { border-color: var(--muted); }
  .sw[aria-checked='true']:hover:not(:disabled) { border-color: var(--accent); filter: brightness(1.1); }
}
@media (prefers-reduced-motion: reduce) {
  .sw::after { transition: background-color var(--dur-fast) ease; }
  .sw:active:not(:disabled) { transform: none; }
}
</style>
