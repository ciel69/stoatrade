<script setup lang="ts">
import { computed } from 'vue'
import { PlanRow, useTradingStore } from '@/entities/trading'
import { UiChip } from '@/shared/ui'
import { useNow } from '@/shared/lib'

/**
 * Планы, которые ждут входа: ближайшие к триггеру сверху. Каждый план нарисован так же, как открытая позиция (полоса стоп, вход, цель, цена), а словами сказано, чего он ждёт,
 * сколько до уровня, когда ближайшая проверка (свеча с отсчётом или цена) и когда план истечёт.
 */
const trading = useTradingStore()
const now = useNow()
const block = computed(() => trading.snapshot?.plans ?? null)

const counts = computed(() => {
  const c = block.value?.counts
  return c ? [{ id: 'waiting', label: 'ждёт входа', n: c.waiting }, { id: 'watching', label: 'далеко от входа', n: c.watching }, { id: 'in_trade', label: 'в сделке', n: c.in_trade }].filter((x) => x.n > 0) : []
})
const active = computed(() => (block.value ? block.value.counts.waiting + block.value.counts.watching + block.value.counts.in_trade : 0))      // закрытые за сутки в «в работе» не входят
</script>

<template>
  <section class="plans" aria-label="Планы в работе">
    <h2 class="title">Планы в работе<span v-if="block" class="n">{{ active }}</span></h2>
    <div v-if="counts.length" class="counts">
      <UiChip v-for="c in counts" :key="c.id" :tone="c.id === 'waiting' ? 'accent' : 'neutral'">{{ c.label }} {{ c.n }}</UiChip>
    </div>

    <ul v-if="block?.near.length" class="list">
      <PlanRow v-for="p in block.near" :key="p.id" :plan="p" :now="now" />
    </ul>
    <p v-else-if="block" class="empty">{{ active ? 'Планов, которые ждут входа, нет.' : 'Активных планов нет.' }}</p>
    <div v-else class="skel" aria-busy="true" />
  </section>
</template>

<style scoped>
.plans { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: var(--space-4); min-width: 0; }
.title { margin: 0; font-size: 14px; font-weight: 600; display: flex; align-items: baseline; gap: var(--space-2); }
.n { font-size: 12px; font-weight: 400; color: var(--muted); }
.counts { display: flex; flex-wrap: wrap; gap: var(--space-2); margin-top: var(--space-2); }
.list { list-style: none; margin: var(--space-3) 0 0; padding: 0; }
.empty { margin: var(--space-3) 0 0; font-size: 13px; color: var(--muted); }
.skel { height: 64px; margin-top: var(--space-3); border-radius: var(--radius-sm); background: var(--panel2); }
</style>
