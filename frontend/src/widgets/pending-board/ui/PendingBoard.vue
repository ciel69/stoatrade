<script setup lang="ts">
import { computed } from 'vue'
import { useTradingStore, type PendingOrder } from '@/entities/trading'
import { UiChip, UiMeter } from '@/shared/ui'
import { countdown, fixed, price, useNow } from '@/shared/lib'

/** Лимитные заявки, которые ждут цены. Шкала: сколько времени заявки осталось из отведённого (ttl); у заявки без срока шкалы нет. */
const trading = useTradingStore()
const now = useNow()
const block = computed(() => trading.snapshot?.pending ?? null)

const left = (o: PendingOrder) => (o.expires_at != null ? Math.max(0, o.expires_at - now.value) : null)
const fill = (o: PendingOrder) => (left(o) != null && o.ttl_min ? left(o)! / (o.ttl_min * 60) : null)
const level = (o: PendingOrder) => ((fill(o) ?? 1) < 0.15 ? 'warn' : 'ok')
const gap = (o: PendingOrder) => (o.dist_pct != null ? `${fixed(Math.abs(o.dist_pct))}% ${o.dist_pct < 0 ? 'ниже' : 'выше'} цены` : '–')
</script>

<template>
  <section class="pend" aria-label="Лимитные заявки">
    <h2 class="title">Лимитные заявки<span v-if="block" class="n">{{ block.total }}</span></h2>

    <ul v-if="block?.items.length" class="list">
      <li v-for="o in block.items" :key="o.id" class="row">
        <div class="top">
          <b class="sym">{{ o.sym }}</b>
          <UiChip>{{ o.side === 'long' ? 'лонг' : 'шорт' }}{{ o.lev ? ' ' + o.lev + '×' : '' }}</UiChip>
          <UiChip v-if="o.mode === 'paper'">paper</UiChip>
          <UiChip v-if="o.stale" tone="warn">цена устарела</UiChip>
          <span class="lim">{{ price(o.price) }}</span>
        </div>
        <p v-if="o.src === 'plan' && o.plan" class="from">из плана {{ o.plan }}</p>
        <p class="sub">сейчас {{ price(o.cur) }} · {{ gap(o) }}<template v-if="o.sl != null"> · стоп {{ price(o.sl) }}</template><template v-if="o.tp != null"> · тейк {{ price(o.tp) }}</template></p>
        <template v-if="fill(o) != null">
          <UiMeter :fill="fill(o)!" :level="level(o)" :label="`${o.sym}: осталось времени заявки`" />
          <p class="left">осталось {{ countdown(left(o)) }}</p>
        </template>
      </li>
    </ul>
    <p v-else-if="block" class="empty">Сейчас нет заявок на вход, которые ждут цены. Как только план выставит лимитный вход на бирже, он появится здесь.</p>
    <div v-else class="skel" aria-busy="true" />
  </section>
</template>

<style scoped>
.pend { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: var(--space-4); min-width: 0; }
.title { margin: 0; font-size: 14px; font-weight: 600; display: flex; align-items: baseline; gap: var(--space-2); }
.n { font-size: 12px; font-weight: 400; color: var(--muted); }
.list { list-style: none; margin: var(--space-3) 0 0; padding: 0; }
.row { padding: var(--space-3) 0; border-top: 1px solid var(--line); display: grid; gap: 6px; }
.row:first-child { border-top: 0; padding-top: 0; }
.top { display: flex; align-items: center; gap: var(--space-2); }
.sym { font-size: 15px; letter-spacing: -0.01em; }
.lim { margin-left: auto; font-size: 15px; font-weight: 700; font-variant-numeric: tabular-nums; }
.sub { margin: 0; font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.from { margin: 0; font-size: 12px; color: var(--muted); overflow-wrap: anywhere; }
.left { margin: 0; font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; text-align: right; }
.empty { margin: var(--space-2) 0 0; font-size: 13px; color: var(--muted); }
.skel { height: 48px; margin-top: var(--space-3); border-radius: var(--radius-sm); background: var(--panel2); }
</style>
