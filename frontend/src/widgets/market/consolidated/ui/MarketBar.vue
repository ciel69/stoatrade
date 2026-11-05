<script setup lang="ts">
import { computed } from 'vue'
import { skewText, tierWord, warnInfo, type CmktCoin, type CmktSnapshot } from '@/entities/cmkt'
import { UiChip, UiTip } from '@/shared/ui'
import AgeLabel from './AgeLabel.vue'

/**
 * Верхняя полоса сводного рынка: выбор монеты, возраст снимка, сколько рынков и монет в сводке, расхождение часов и замечания.
 * Возраст живёт в отдельном AgeLabel, чтобы тик раз в секунду не перерисовывал список монет и стакан.
 */
const props = defineProps<{ snapshot: CmktSnapshot; selected: string | null; stale?: boolean }>()
const emit = defineEmits<{ select: [sym: string] }>()

const deep = computed<CmktCoin[]>(() => props.snapshot.coins.filter((c) => c.tier === 'deep'))
const light = computed<CmktCoin[]>(() => props.snapshot.coins.filter((c) => c.tier !== 'deep'))
const warnings = computed(() => [...new Set(props.snapshot.warn)].map(warnInfo))
const perp = computed(() => props.snapshot.n_ex.perp)
const spot = computed(() => props.snapshot.n_ex.spot)

function changed(e: Event): void {
  const v = (e.target as HTMLSelectElement).value
  if (v) emit('select', v)
}
</script>

<template>
  <header class="bar">
    <div class="line">
      <h2 class="h">Сводный стакан</h2>
      <label v-if="!stale" class="pick">
        <span class="pick-k">Монета</span>
        <select :value="selected ?? ''" @change="changed">
          <option value="" disabled>выберите монету</option>
          <optgroup v-if="deep.length" label="Глубокие">
            <option v-for="c in deep" :key="c.sym" :value="c.sym">{{ c.sym }} · {{ tierWord(c.tier) }}</option>
          </optgroup>
          <optgroup v-if="light.length" label="Лёгкие">
            <option v-for="c in light" :key="c.sym" :value="c.sym">{{ c.sym }} · {{ tierWord(c.tier) }}</option>
          </optgroup>
        </select>
      </label>
      <AgeLabel :ts="snapshot.ts" />
      <UiTip v-for="w in warnings" :key="w.word" :tip="{ title: w.word, text: w.about }">
        <UiChip :tone="w.tone">{{ w.word }}</UiChip>
      </UiTip>
    </div>
    <p v-if="!stale" class="meta">
      <span>{{ perp }} перп · {{ spot }} спот</span>
      <span>{{ snapshot.n_syms }} монет в фокусе</span>
      <span>часы {{ skewText(snapshot.skew_ms) }}</span>
      <span v-if="!selected" class="hint">Выберите монету — покажем её стакан.</span>
    </p>
  </header>
</template>

<style scoped>
.bar { display: grid; gap: 6px; min-width: 0; }
.line { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-2) var(--space-3); min-width: 0; }
.h { margin: 0; margin-right: auto; font-size: 14px; font-weight: 600; }
.pick { display: inline-flex; align-items: center; gap: var(--space-2); min-width: 0; }
.pick-k { font-size: 12px; color: var(--muted); white-space: nowrap; }
select { height: 32px; max-width: 260px; padding: 0 var(--space-2); border: 1px solid var(--line2); border-radius: var(--radius-sm); background: var(--panel2); color: var(--text); font: inherit; font-size: 13px; font-weight: 600; cursor: pointer; }
select:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.meta { margin: 0; display: flex; flex-wrap: wrap; gap: 2px var(--space-4); font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.hint { color: var(--muted); }
@media (max-width: 480px) {
  select { max-width: 100%; }
  .pick { width: 100%; }
  select { flex: 1 1 auto; }
}
</style>
