<script setup lang="ts">
import { computed } from 'vue'
import { swapBy, swapText, swapWhen, useRoleStore } from '@/entities/role'
import { UiDetail } from '@/shared/ui'

/**
 * Боковая панель «История назначений»: кто и когда менял модели ролей, новые сверху. Откатить отсюда нельзя: кнопка появится после входа по токену.
 * Номера ревизий (до и после смены) спрятаны мелким текстом под строкой.
 */
defineEmits<{ close: [] }>()
const store = useRoleStore()
const swaps = computed(() => store.snapshot?.swaps ?? [])
</script>

<template>
  <UiDetail label="История назначений" @close="$emit('close')">
    <template #title><b class="h">История назначений</b></template>
    <p class="what">Когда и кем менялись модели ролей мозга. Новые изменения сверху.</p>
    <ol v-if="swaps.length" class="list">
      <li v-for="s in swaps" :key="s.seq">
        <p class="when">{{ swapWhen(s.ts) }} · {{ swapBy(s.by) }}</p>
        <p class="did">{{ swapText(s) }}</p>
        <p class="rev">ревизия {{ s.prev_rev ?? '–' }} → {{ s.rev ?? '–' }}{{ s.tier ? ' · ' + s.tier : '' }}</p>
      </li>
    </ol>
    <p v-else class="meta">Смен назначений ещё не было.</p>
    <p class="locked">Откат появится после входа по токену.</p>
  </UiDetail>
</template>

<style scoped>
.h { font-size: 17px; }
.what { margin: 0; font-size: 13px; line-height: 1.5; }
.list { list-style: none; margin: 0; padding: 0; display: grid; gap: var(--space-3); }
.list li + li { border-top: 1px solid var(--line); padding-top: var(--space-3); }
.when { margin: 0; font-size: 12px; color: var(--muted); }
.did { margin: 2px 0; font-size: 13px; overflow-wrap: anywhere; }
.rev { margin: 0; font-size: 11px; color: var(--muted); font-family: var(--mono, ui-monospace, monospace); }
.meta { margin: 0; font-size: 12px; color: var(--muted); }
.locked { margin: 0; padding: var(--space-3); border-radius: var(--radius-sm); background: var(--panel2); font-size: 12px; color: var(--muted); line-height: 1.5; }
</style>
