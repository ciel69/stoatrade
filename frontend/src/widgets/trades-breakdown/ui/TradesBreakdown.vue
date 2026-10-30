<script setup lang="ts">
import { computed } from 'vue'
import { useTradingStore, type TradeGroup } from '@/entities/trading'
import { signed } from '@/shared/lib'

/** Как закрываются сделки и в чём ошибки: длина полосы это число сделок (цвет нейтральный), результат группы подписан числом. */
const trading = useTradingStore()
const t = computed(() => trading.trades)
const REASON: Record<string, string> = { sl: 'стоп', tp: 'тейк', manual: 'вручную', time: 'по времени' }
const max = (g: TradeGroup[]) => Math.max(1, ...g.map((x) => x.n))
</script>

<template>
  <section class="bd" aria-label="Причины выхода и ошибки">
    <h2 class="title">Причины выхода</h2>
    <ul v-if="t?.reasons.length" class="list">
      <li v-for="g in t.reasons" :key="g.name" class="row">
        <span class="name">{{ REASON[g.name] ?? g.name }}</span>
        <span class="track"><i :style="{ width: (g.n / max(t.reasons)) * 100 + '%' }" /></span>
        <span class="n">{{ g.n }}</span>
        <span class="pnl" :class="(g.pnl ?? 0) > 0 ? 'up' : (g.pnl ?? 0) < 0 ? 'down' : ''">{{ signed(g.pnl) }}</span>
      </li>
    </ul>
    <p v-else class="empty">{{ t ? 'Нет закрытых сделок.' : '' }}</p>

    <h2 class="title second">Теги ошибок из разбора</h2>
    <ul v-if="t?.errs.length" class="list">
      <li v-for="g in t.errs" :key="g.name" class="row">
        <span class="name">{{ g.name }}</span>
        <span class="track"><i :style="{ width: (g.n / max(t.errs)) * 100 + '%' }" /></span>
        <span class="n">{{ g.n }}</span>
        <span class="pnl" :class="(g.pnl ?? 0) > 0 ? 'up' : (g.pnl ?? 0) < 0 ? 'down' : ''">{{ signed(g.pnl) }}</span>
      </li>
    </ul>
    <p v-else-if="t" class="empty">Разбор ещё не записан. Теги ставятся командой <code>./tb journal post</code> после закрытия сделки.</p>
    <p v-if="t?.errs.length && t.unreviewed" class="note">Без разбора: {{ t.unreviewed }}</p>
  </section>
</template>

<style scoped>
.bd { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: var(--space-4); min-width: 0; }
.title { margin: 0 0 var(--space-3); font-size: 14px; font-weight: 600; }
.title.second { margin-top: var(--space-5); }
.list { list-style: none; margin: 0; padding: 0; display: grid; gap: var(--space-2); }
.row { display: grid; grid-template-columns: minmax(70px, 34%) minmax(0, 1fr) auto 56px; gap: var(--space-3); align-items: center; font-size: 13px; }
.name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.track { height: 8px; border-radius: 99px; background: var(--line); overflow: hidden; }
.track i { display: block; height: 100%; border-radius: 99px; background: linear-gradient(90deg, color-mix(in srgb, var(--accent) 55%, transparent), var(--accent)); }
.n { color: var(--muted); font-size: 12px; font-variant-numeric: tabular-nums; }
.pnl { text-align: right; font-weight: 600; font-variant-numeric: tabular-nums; }
.up { color: var(--pos); }
.down { color: var(--neg); }
.empty { margin: 0; color: var(--muted); font-size: 13px; }
.note { margin: var(--space-2) 0 0; color: var(--muted); font-size: 12px; }
code { font-size: 12px; }
</style>
