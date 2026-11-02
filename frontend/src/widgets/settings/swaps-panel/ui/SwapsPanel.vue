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
  <UiDetail label="История смен моделей" @close="$emit('close')">
    <template #title><b class="h">История смен моделей</b></template>
    <p class="what">Когда и кем менялись модели ролей мозга. Новые изменения сверху.</p>
    <ol v-if="swaps.length" class="list">
      <li v-for="s in swaps" :key="s.seq">
        <p class="when">{{ swapWhen(s.ts) }} · {{ swapBy(s.by) }}</p>
        <p class="did">{{ swapText(s) }}</p>
        <details class="rev-d"><summary>Для специалиста</summary><p class="rev">ревизия {{ s.prev_rev ?? '–' }} → {{ s.rev ?? '–' }}{{ s.tier ? ' · ' + s.tier : '' }}</p></details>
      </li>
    </ol>
    <p v-else class="meta">Смен моделей ещё не было.</p>
    <p class="locked">Откатить смену отсюда пока нельзя: сменить модель обратно можно в панели роли.</p>
  </UiDetail>
</template>

<style scoped>
.h { font-size: 17px; }
.what { margin: 0; font-size: 13px; line-height: 1.5; }
.list { list-style: none; margin: 0; padding: 0; display: grid; gap: var(--space-3); }
.list li + li { border-top: 1px solid var(--line); padding-top: var(--space-3); }
.when { margin: 0; font-size: 12px; color: var(--muted); }
.did { margin: 2px 0; font-size: 13px; overflow-wrap: anywhere; }
.rev-d { font-size: 12px; color: var(--muted); }
.rev-d summary { cursor: pointer; min-height: 28px; display: flex; align-items: center; }
.rev-d summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 2px; }
.rev { margin: 0; font-size: 11px; color: var(--muted); font-family: var(--mono, ui-monospace, monospace); }
.meta { margin: 0; font-size: 12px; color: var(--muted); }
.locked { margin: 0; padding: var(--space-3); border-radius: var(--radius-sm); background: var(--panel2); font-size: 12px; color: var(--muted); line-height: 1.5; }
</style>
