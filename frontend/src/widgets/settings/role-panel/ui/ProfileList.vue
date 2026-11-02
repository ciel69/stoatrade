<script setup lang="ts">
import { placeOf, profileBlock, type ProfileLike } from '@/entities/role'

/**
 * Список профилей моделей для выбора прямо в панели роли: название, где работает, состояние провайдера, допуск к live.
 * Профиль с выключенным провайдером показан, но не выбирается (причина в строке). liveWarn: роль влияет на live, платформа в live,
 * поэтому «не допущена к live» подсвечена: сервер может отказать, это покажет общий диалог.
 */
defineProps<{ items: ProfileLike[]; current?: string | null; selected?: string | null; liveWarn: boolean; disabled: boolean; label: string }>()
const emit = defineEmits<{ pick: [id: string] }>()
</script>

<template>
  <ul class="pl" :aria-label="label">
    <li v-for="p in items" :key="p.id">
      <button type="button" class="row" :class="{ on: p.id === selected }" :disabled="disabled || !!profileBlock(p)" :aria-pressed="selected !== undefined ? p.id === selected : undefined" @click="emit('pick', p.id)">
        <span class="top">
          <b>{{ p.model_name }}</b>
          <i v-if="p.id === selected" class="tag sel">выбрана</i>
          <i v-else-if="p.id === current" class="tag">текущая</i>
        </span>
        <span class="meta">{{ placeOf(p) }} · {{ p.provider }}</span>
        <span class="meta">
          <template v-if="profileBlock(p)"><em class="warn">{{ profileBlock(p) }}</em></template>
          <template v-else>провайдер включён</template>
          · <em :class="{ warn: liveWarn && !p.live_ok }">{{ p.live_ok ? 'допущена к реальным деньгам' : 'не допущена к реальным деньгам' }}</em>
        </span>
      </button>
    </li>
    <li v-if="!items.length" class="none">Подходящих моделей нет.</li>
  </ul>
</template>

<style scoped>
.pl { list-style: none; margin: 0; padding: 0; display: grid; gap: var(--space-1); }
.row { width: 100%; display: grid; gap: 2px; padding: var(--space-2) var(--space-3); text-align: left; font: inherit; color: var(--text); background: var(--panel2); border: 1px solid transparent; border-radius: var(--radius-sm); cursor: pointer; transition: border-color var(--dur-fast) ease, background-color var(--dur-fast) ease, transform 120ms var(--ease-out); }
.row.on { border-color: var(--accent); background: var(--accent-bg); }
.row:disabled { cursor: not-allowed; opacity: 0.6; }
.row:active:not(:disabled) { transform: scale(0.99); }
.top { display: flex; align-items: baseline; gap: var(--space-2); }
.top b { font-size: 13px; overflow-wrap: anywhere; }
.tag { font-style: normal; font-size: 11px; color: var(--muted); }
.tag.sel { color: var(--accent); }
.meta { font-size: 12px; color: var(--muted); line-height: 1.4; }
em { font-style: normal; }
.warn { color: var(--warn); }
.none { font-size: 12px; color: var(--muted); padding: var(--space-2) 0; }
@media (hover: hover) and (pointer: fine) {
  .row:hover:not(:disabled):not(.on) { border-color: var(--line2); }
}
.row:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
@media (prefers-reduced-motion: reduce) {
  .row { transition: border-color var(--dur-fast) ease, background-color var(--dur-fast) ease; }
}
</style>
