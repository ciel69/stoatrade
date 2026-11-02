<script setup lang="ts">
import { ref } from 'vue'
import { THEME_PREFS, applyTheme, readTheme, saveTheme, type ThemePref } from '@/shared/lib'

/**
 * Переключатель темы: «Как в системе», «Светлая», «Тёмная». Выбор применяется сразу (атрибут data-theme на :root) и запоминается в localStorage;
 * до первого рендера тему ставит public/theme-init.js, поэтому мигания нет. Кнопки обычные, доступны с клавиатуры, значок без текста, имя в aria-label.
 */
const pref = ref<ThemePref>(readTheme())

function pick(next: ThemePref): void {
  pref.value = next
  applyTheme(next)
  saveTheme(next)
}
</script>

<template>
  <div class="theme" role="group" aria-label="Тема оформления">
    <button
      v-for="o in THEME_PREFS"
      :key="o.value"
      type="button"
      class="opt"
      :class="{ on: pref === o.value }"
      :aria-pressed="pref === o.value"
      :aria-label="o.label"
      :title="o.label"
      @click="pick(o.value)"
    >
      <svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <template v-if="o.value === 'system'"><rect x="2" y="3" width="12" height="8" rx="1.5" /><path d="M6 14h4M8 11v3" /></template>
        <template v-else-if="o.value === 'light'"><circle cx="8" cy="8" r="2.8" /><path d="M8 1.5v1.6M8 12.9v1.6M1.5 8h1.6M12.9 8h1.6M3.4 3.4l1.1 1.1M11.5 11.5l1.1 1.1M3.4 12.6l1.1-1.1M11.5 4.5l1.1-1.1" /></template>
        <path v-else d="M13.2 9.6A5.6 5.6 0 0 1 6.4 2.8a5.6 5.6 0 1 0 6.8 6.8Z" />
      </svg>
    </button>
  </div>
</template>

<style scoped>
.theme { display: inline-flex; background: var(--panel2); border: 1px solid var(--line); border-radius: var(--radius-sm); padding: 2px; gap: 2px; }
.opt { display: grid; place-items: center; width: 28px; height: 24px; padding: 0; border: 0; border-radius: 4px; background: transparent; color: var(--muted); cursor: pointer; transition: color var(--dur-fast) ease, background-color var(--dur-fast) ease; }
.opt.on { background: var(--accent-bg); color: var(--accent); }
.opt:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
@media (hover: hover) and (pointer: fine) {
  .opt:not(.on):hover { color: var(--text); }
}
@media (prefers-reduced-motion: reduce) {
  .opt { transition: color var(--dur-fast) ease; }
}
</style>
