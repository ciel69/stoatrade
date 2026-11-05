<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'
import { useCmktStore } from '@/entities/cmkt'
import MarketBar from './MarketBar.vue'
import MarketFacts from './MarketFacts.vue'
import OrderBook from './OrderBook.vue'

/**
 * Сводный рынок: одна полоса выбора и состояния, стакан выбранной монеты и факты рядом. Возраст тикает только в своей метке,
 * поэтому экран не перерисовывается раз в секунду; данные обновляются снимком (раз в 5 с) и ответом по монете.
 */
const props = defineProps<{ selected: string | null }>()
const emit = defineEmits<{ select: [sym: string] }>()

const store = useCmktStore()
onMounted(() => store.start())
watch(() => props.selected, (sym) => store.select(sym), { immediate: true })

const snapshot = computed(() => store.snapshot)
const stale = computed(() => !!snapshot.value && !snapshot.value.ok)
const detail = computed(() => {
  const d = store.detail
  if (!d || !props.selected) return null
  return d.sym?.toUpperCase() === props.selected.toUpperCase() ? d : null
})
</script>

<template>
  <section class="cmkt" aria-label="Сводный рынок">
    <p v-if="!snapshot && store.link !== 'open'" class="state">Нет связи с платформой: сводный рынок придёт, когда соединение восстановится.</p>
    <div v-else-if="!snapshot" class="skel" aria-busy="true"><span class="sr">Загружаем сводный рынок…</span></div>
    <template v-else>
      <MarketBar :snapshot="snapshot" :selected="selected" :stale="stale" @select="(sym) => emit('select', sym)" />
      <p v-if="stale" class="state warn">Снимок сводного рынка устарел (старше 5 секунд): числа скрыты, пока сервис не пришлёт свежий. Страница обновится сама.</p>
      <div v-else class="grid">
        <OrderBook :sym="selected" :detail="detail" :loading="store.loading" :link="store.link" />
        <MarketFacts v-if="detail" :detail="detail" />
        <p v-else class="hint">Факты по монете появятся вместе со стаканом.</p>
      </div>
    </template>
  </section>
</template>

<style scoped>
.cmkt { display: grid; gap: var(--space-3); min-width: 0; padding: var(--space-4); background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); }
.grid { display: grid; grid-template-columns: minmax(0, 1.35fr) minmax(0, 0.9fr); gap: var(--space-4); align-items: start; min-width: 0; }
.state { margin: 0; padding: var(--space-2) 0; font-size: 13px; line-height: 1.5; color: var(--muted); max-width: 78ch; }
.state.warn { color: var(--warn); }
.hint { margin: 0; font-size: 12px; color: var(--muted); }
.skel { height: 420px; border-radius: var(--radius-sm); background: var(--panel2); }
.sr { position: absolute; width: 1px; height: 1px; margin: -1px; padding: 0; overflow: hidden; clip-path: inset(50%); white-space: nowrap; border: 0; }
@media (max-width: 1000px) {
  .grid { grid-template-columns: minmax(0, 1fr); }
}
@media (max-width: 480px) {
  .cmkt { padding: var(--space-3); }
}
</style>
