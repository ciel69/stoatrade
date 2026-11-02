<script setup lang="ts">
export type ChipTone = 'neutral' | 'ok' | 'warn' | 'bad' | 'accent'
withDefaults(defineProps<{ tone?: ChipTone }>(), { tone: 'neutral' })
</script>

<template>
  <span class="chip" :class="`tone-${tone}`"><slot /></span>
</template>

<style scoped>
.chip {
  display: inline-block;
  padding: 1px 8px;
  border: 1px solid var(--line2);
  border-radius: 99px;
  background: var(--panel2);
  color: var(--muted);
  font-size: 11px;
  line-height: 1.6;
  white-space: nowrap;
  transition: color var(--dur-fast) ease, border-color var(--dur-fast) ease, background-color var(--dur-fast) ease;
}
.tone-ok { color: var(--pos); border-color: var(--pos); }
.tone-warn { color: var(--warn); border-color: var(--warn); }
.tone-bad { color: var(--neg); border-color: var(--neg); }
.tone-accent { color: var(--accent); border-color: var(--accent); background: var(--accent-bg); }
</style>
