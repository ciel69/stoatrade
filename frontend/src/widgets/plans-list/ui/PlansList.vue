<script setup lang="ts">
import { computed, ref } from 'vue'
import { PlanClosedRow, PlanRow, PositionRow, useTradingStore, type NearPlan } from '@/entities/trading'
import { UiSegment } from '@/shared/ui'
import { useNow } from '@/shared/lib'

/**
 * Все планы режима одним списком: ближайшие к входу сверху, затем в сделке (та же полоса, что у позиции) и закрытые за сутки. Переключатель по состоянию со счётчиками.
 * Строка выбирается кликом или Enter: выбранный план открывается в боковой панели (её держит страница).
 */
const props = defineProps<{ selected: string | null }>()
const emit = defineEmits<{ select: [id: string] }>()
const trading = useTradingStore()
const now = useNow()

type Filter = 'all' | 'waiting' | 'watching' | 'in_trade' | 'closed'
const filter = ref<Filter>('all')
const block = computed(() => trading.plans)
const options = computed(() => {
  const c = block.value?.counts
  const n = (k: keyof NonNullable<typeof c>) => (c ? ` ${c[k]}` : '')
  return [
    { value: 'all' as Filter, label: `Все${block.value ? ' ' + block.value.total : ''}` },
    { value: 'waiting' as Filter, label: `Ждёт входа${n('waiting')}` },
    { value: 'watching' as Filter, label: `Далеко${n('watching')}` },
    { value: 'in_trade' as Filter, label: `В сделке${n('in_trade')}` },
    { value: 'closed' as Filter, label: `Закрытые${n('closed')}` },
  ]
})
const items = computed(() => (block.value?.items ?? []).filter((p) => filter.value === 'all' || p.col === filter.value))
const positionOf = (p: NearPlan) => trading.positions.find((x) => x.plan === p.id) ?? null
const empty = computed(() => (filter.value === 'all' ? 'Планов пока нет: мозг создаёт их командой ./tb plan submit, исполнитель ведёт по условиям.' : 'В этом состоянии планов нет.'))
</script>

<template>
  <section class="plans" aria-label="Планы">
    <header class="top">
      <h2 class="title">Планы</h2>
      <UiSegment v-model="filter" :options="options" label="Состояние плана" />
    </header>

    <ul v-if="items.length" class="list">
      <template v-for="p in items" :key="p.id">
        <PositionRow v-if="p.col === 'in_trade' && positionOf(p)" :position="positionOf(p)!" class="pick" :class="{ sel: props.selected === p.id }" tabindex="0" role="button"
                     :aria-pressed="props.selected === p.id" @click="emit('select', p.id)" @keydown.enter.self="emit('select', p.id)" />
        <PlanClosedRow v-else-if="p.col === 'in_trade' || p.col === 'closed'" :plan="p" class="pick" :class="{ sel: props.selected === p.id }" tabindex="0" role="button"
                       :aria-pressed="props.selected === p.id" @click="emit('select', p.id)" @keydown.enter.self="emit('select', p.id)" />
        <PlanRow v-else :plan="p" :now="now" class="pick" :class="{ sel: props.selected === p.id }" tabindex="0" role="button"
                 :aria-pressed="props.selected === p.id" @click="emit('select', p.id)" @keydown.enter.self="emit('select', p.id)" />
      </template>
    </ul>
    <p v-else-if="block" class="empty">{{ empty }}</p>
    <div v-else class="skel" aria-busy="true" />
  </section>
</template>

<style scoped>
.plans { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: var(--space-4); min-width: 0; }
.top { display: flex; align-items: center; flex-wrap: wrap; gap: var(--space-3); }
.title { margin: 0; font-size: 14px; font-weight: 600; margin-right: auto; }
/* строки идут сеткой: сколько влезает колонок по 460 px; разделитель снизу, нижняя линия последнего ряда срезана clip-path */
.list { list-style: none; margin: var(--space-3) 0 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(min(460px, 100%), 1fr)); column-gap: var(--space-6); clip-path: inset(0 calc(var(--space-3) * -1) 1px calc(var(--space-3) * -1)); }
.list > :deep(li) { border-top: 0; border-bottom: 1px solid var(--line); }
.list > :deep(li:first-child) { padding-top: var(--space-3); }
/* строка кликабельна: поля внутрь, чтобы выделение не упиралось в текст */
.list > :deep(li.pick) { cursor: pointer; padding-inline: var(--space-3); margin-inline: calc(var(--space-3) * -1); border-radius: var(--radius-sm); transition: background-color var(--dur-fast) ease; }
.list > :deep(li.pick.sel) { background: color-mix(in srgb, var(--accent) 9%, var(--panel)); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent) 35%, transparent); }
.list > :deep(li.pick:focus-visible) { outline: 2px solid var(--accent); outline-offset: -2px; }
@media (hover: hover) and (pointer: fine) {
  .list > :deep(li.pick:not(.sel):hover) { background: var(--panel2); }
}
.empty { margin: var(--space-3) 0 0; font-size: 13px; color: var(--muted); }
.skel { height: 96px; margin-top: var(--space-3); border-radius: var(--radius-sm); background: var(--panel2); }
</style>
