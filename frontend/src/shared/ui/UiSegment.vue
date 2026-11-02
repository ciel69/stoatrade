<script setup lang="ts" generic="T extends string">
/** Переключатель-сегмент (режим, период): кнопки одной группы, текущая отмечена aria-pressed. Стрелки и Tab как у обычных кнопок. muted: вариант выбрать можно, но он недоступен (серый), причину показывает страница. */
defineProps<{ options: ReadonlyArray<{ value: T; label: string; muted?: boolean }>; modelValue: T; label?: string }>()
const emit = defineEmits<{ 'update:modelValue': [value: T] }>()
</script>

<template>
  <div class="seg" role="group" :aria-label="label">
    <button v-for="o in options" :key="o.value" type="button" class="opt" :class="{ on: o.value === modelValue, muted: o.muted }" :aria-pressed="o.value === modelValue" @click="emit('update:modelValue', o.value)">
      {{ o.label }}
    </button>
  </div>
</template>

<style scoped>
.seg { display: inline-flex; flex-wrap: wrap; background: var(--panel2); border: 1px solid var(--line); border-radius: var(--radius-sm); padding: 2px; gap: 2px; }
.opt {
  border: 0;
  background: transparent;
  color: var(--muted);
  padding: 3px 10px;
  border-radius: 4px;
  font-size: 12px;
  cursor: pointer;
  transition: color var(--dur-fast) ease, background-color var(--dur-fast) ease;
}
.opt.on { background: var(--accent-bg); color: var(--accent); }
.opt.muted { opacity: 0.55; }
.opt.muted.on { background: var(--panel); color: var(--muted); opacity: 1; }
@media (hover: hover) and (pointer: fine) {
  .opt:not(.on):hover { color: var(--text); }
}
</style>
