<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { TradeCardView, useTradingStore } from '@/entities/trading'
import { UiSegment } from '@/shared/ui'

/** История закрытых сделок режима: фильтр по результату, карточки (разбор открывает страница боковой панелью), «показать ещё» порциями. */
type Res = 'all' | 'win' | 'loss'
defineProps<{ selected: string | null }>()
const emit = defineEmits<{ select: [id: string] }>()
const FIRST = 12
const STEP = 24
const trading = useTradingStore()
const res = ref<Res>('all')
const shown = ref(FIRST)
watch(res, () => { shown.value = FIRST })

const all = computed(() => trading.trades?.items ?? [])
const wins = computed(() => all.value.filter((t) => (t.pnl ?? 0) > 0).length)
const filtered = computed(() => all.value.filter((t) => (res.value === 'win' ? (t.pnl ?? 0) > 0 : res.value === 'loss' ? (t.pnl ?? 0) <= 0 : true)))
const visible = computed(() => filtered.value.slice(0, shown.value))
const options = computed(() => [
  { value: 'all', label: `Все ${all.value.length}` },
  { value: 'win', label: `Плюс ${wins.value}` },
  { value: 'loss', label: `Минус ${all.value.length - wins.value}` },
] as const)
const older = computed(() => Math.max(0, (trading.trades?.total ?? 0) - all.value.length))
</script>

<template>
  <section class="hist" aria-label="История сделок">
    <header class="head">
      <h2 class="title">История сделок<span v-if="trading.trades" class="n">{{ trading.trades.total }}</span></h2>
      <UiSegment v-model="res" :options="options" label="Фильтр по результату" class="seg" />
    </header>

    <div v-if="visible.length" class="grid">
      <TradeCardView v-for="t in visible" :key="t.id" :trade="t" :selected="selected === t.id" @select="emit('select', t.id)" />
    </div>
    <p v-else-if="trading.trades" class="empty">{{ all.length ? 'Нет сделок по этому фильтру.' : 'Закрытых сделок в этом режиме ещё нет.' }}</p>
    <div v-else class="skel" aria-busy="true" />

    <div v-if="filtered.length > FIRST" class="more">
      <button v-if="shown < filtered.length" type="button" class="btn" @click="shown += STEP">Показать ещё {{ Math.min(STEP, filtered.length - shown) }}</button>
      <button v-else type="button" class="btn" @click="shown = FIRST">Свернуть до {{ FIRST }}</button>
    </div>
    <p v-if="older && res === 'all' && shown >= filtered.length" class="older">Ещё {{ older }} более ранних сделок есть в журнале.</p>
  </section>
</template>

<style scoped>
.hist { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: var(--space-4); min-width: 0; }
.head { display: flex; align-items: center; gap: var(--space-3); margin-bottom: var(--space-3); flex-wrap: wrap; }
.title { margin: 0; font-size: 14px; font-weight: 600; display: flex; align-items: baseline; gap: var(--space-2); }
.n { font-size: 12px; font-weight: 400; color: var(--muted); }
.seg { margin-left: auto; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: var(--space-3); }
.empty { margin: 0; padding: var(--space-5) 0; text-align: center; color: var(--muted); font-size: 13px; }
.skel { height: 160px; border-radius: var(--radius-sm); background: var(--panel2); }
.more { display: flex; justify-content: center; margin-top: var(--space-4); }
.btn { padding: 6px 16px; background: var(--panel2); color: var(--text); border: 1px solid var(--line2); border-radius: var(--radius-sm); font-size: 12px; cursor: pointer; transition: border-color var(--dur-fast) ease; }
@media (hover: hover) and (pointer: fine) {
  .btn:hover { border-color: var(--muted); }
}
.older { margin: var(--space-3) 0 0; text-align: center; font-size: 12px; color: var(--muted); }
</style>
