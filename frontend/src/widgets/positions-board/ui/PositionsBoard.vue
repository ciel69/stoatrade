<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { PositionRow, useTradingStore } from '@/entities/trading'

const trading = useTradingStore()
onMounted(() => trading.start())
const more = computed(() => Math.max(0, (trading.snapshot?.positions_total ?? 0) - trading.positions.length))
</script>

<template>
  <section class="board" aria-label="Позиции">
    <h2 class="title">Открытые позиции<span v-if="trading.snapshot" class="n">{{ trading.snapshot.positions_total }}</span></h2>
    <ul v-if="trading.positions.length" class="list">
      <PositionRow v-for="p in trading.positions" :key="p.plan ?? p.sym" :position="p" />
    </ul>
    <p v-else-if="trading.snapshot" class="empty">Позиций нет. Стоп и тейк появятся здесь, когда мозг откроет сделку.</p>
    <div v-else class="skel" aria-busy="true" />
    <p v-if="more" class="more">ещё {{ more }}</p>
  </section>
</template>

<style scoped>
.board { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: var(--space-4); min-width: 0; }
.title { margin: 0 0 var(--space-3); font-size: 14px; font-weight: 600; display: flex; align-items: baseline; gap: var(--space-2); }
.n { font-size: 12px; font-weight: 400; color: var(--muted); }
.list { list-style: none; margin: 0; padding: 0; }
.empty { margin: 0; padding: var(--space-5) 0; text-align: center; color: var(--muted); font-size: 13px; }
.more { margin: var(--space-2) 0 0; font-size: 12px; color: var(--muted); }
.skel { height: 96px; border-radius: var(--radius-sm); background: var(--panel2); }
</style>
