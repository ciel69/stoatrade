<script setup lang="ts">
import { computed } from 'vue'
import { roomFill, roomText, useSystemStore } from '@/entities/system'
import { UiChip, UiMeter } from '@/shared/ui'
import { countdown, fixed, signed, useNow } from '@/shared/lib'

/**
 * Три ответа на вопрос «сколько ещё можно»: сессия (идёт ли, входы разрешены, сделки и бюджет убытка), запас до каждого лимита шкалами и капитал (просадка от пика, свободные средства).
 * Лимиты только показываются: менять их здесь нельзя и не нужно (config/risk_profile.json, уровень риска задаёт владелец).
 */
const sys = useSystemStore()
const now = useNow()
const s = computed(() => sys.snapshot)
const sess = computed(() => s.value?.session ?? null)
const left = computed(() => (sess.value?.expires_at != null ? sess.value.expires_at - now.value : null))
const budgetFill = computed(() => (sess.value?.budget_usdt ? Math.max(0, 1 - (sess.value.budget_used ?? 0) / sess.value.budget_usdt) : 0))
const venues = computed(() => Object.entries(s.value?.account.venues ?? {}).filter(([, v]) => (v.equity ?? 0) > 0))
const tone = (x: number | null | undefined) => (x == null || x === 0 ? '' : x > 0 ? 'up' : 'down')
</script>

<template>
  <section v-if="s" class="lim" aria-label="Сессия, запас и капитал">
    <article class="card">
      <h3 class="cap">Сессия</h3>
      <template v-if="sess">
        <p class="big">{{ sess.type }} <UiChip :tone="sess.entries_allowed ? 'ok' : 'warn'">{{ sess.entries_allowed ? 'входы разрешены' : 'входы запрещены' }}</UiChip></p>
        <p v-if="sess.block_reasons.length" class="muted">Причина: {{ sess.block_reasons.join(', ') }}</p>
        <div class="kv">
          <span>Сделок</span><b>{{ sess.trades ?? '–' }}<template v-if="sess.max_trades"> из {{ sess.max_trades }}</template></b>
          <span>Результат</span><b :class="tone(sess.realized)">{{ signed(sess.realized, 2) }} USDT</b>
          <span>Риск открытых</span><b>{{ fixed(sess.open_risk) }} USDT</b>
          <template v-if="left != null"><span>Осталось</span><b>{{ countdown(left) }}</b></template>
        </div>
        <div class="bud"><span>Бюджет убытка</span><span>{{ fixed(sess.budget_used) }} из {{ fixed(sess.budget_usdt) }} USDT</span></div>
        <UiMeter :fill="budgetFill" :level="budgetFill < 0.15 ? 'bad' : budgetFill < 0.35 ? 'warn' : 'ok'" label="Остаток бюджета убытка сессии" />
        <p v-if="sess.thesis" class="thesis">{{ sess.thesis }}</p>
      </template>
      <p v-else class="muted">Активной сессии нет. Входы разрешены только внутри сессии: она создаётся запуском дня.</p>
    </article>

    <article class="card">
      <h3 class="cap">Запас до лимитов<UiChip v-if="s.room.allowed === false" tone="bad">входы закрыты</UiChip></h3>
      <ul v-if="s.room.rows.length" class="rows">
        <li v-for="r in s.room.rows" :key="r.id" :title="r.help">
          <span class="top"><span>{{ r.label }}</span><b>{{ roomText(r) }}</b></span>
          <UiMeter :fill="roomFill(r)" :level="r.level === 'info' ? 'ok' : r.level" :label="r.label" />
        </li>
      </ul>
      <p v-else class="muted">Нет данных о лимитах (config/risk_profile.json).</p>
      <p v-if="s.room.reasons.length" class="muted">Входы закрыты: {{ s.room.reasons.join('; ') }}</p>
    </article>

    <article class="card">
      <h3 class="cap">Капитал</h3>
      <p class="big">{{ fixed(s.account.equity) }} <small>USDT</small></p>
      <div class="kv">
        <span>Просадка от пика</span><b :class="(s.account.drawdown ?? 0) > 0.1 ? 'down' : ''">{{ s.account.drawdown != null ? fixed(s.account.drawdown * 100, 1) + '%' : '–' }}</b>
        <span>Пик</span><b>{{ fixed(s.account.hwm) }}</b>
        <template v-for="[name, v] in venues" :key="name"><span>{{ name }}: свободно</span><b>{{ fixed(v.free) }} из {{ fixed(v.equity) }}</b></template>
        <template v-if="s.account.ladder_mult != null"><span>Множитель размера</span><b>×{{ fixed(s.account.ladder_mult) }}</b></template>
      </div>
    </article>
  </section>
  <div v-else class="skel" aria-busy="true" />
</template>

<style scoped>
.lim { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: var(--space-3); }
.card { display: grid; gap: var(--space-3); align-content: start; padding: var(--space-4); background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); min-width: 0; }
.cap { margin: 0; display: flex; align-items: center; gap: var(--space-2); font-size: 12px; font-weight: 600; color: var(--muted); }
.big { margin: 0; display: flex; align-items: center; gap: var(--space-2); font-size: 22px; font-weight: 700; letter-spacing: -0.01em; font-variant-numeric: tabular-nums; }
.big small { font-size: 12px; font-weight: 400; color: var(--muted); }
.muted { margin: 0; font-size: 12px; color: var(--muted); line-height: 1.5; }
.kv { display: grid; grid-template-columns: 1fr auto; gap: 5px var(--space-3); font-size: 12px; font-variant-numeric: tabular-nums; }
.kv span { color: var(--muted); }
.kv b { text-align: right; font-weight: 600; }
.up { color: var(--pos); }
.down { color: var(--neg); }
.bud { display: flex; justify-content: space-between; font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.thesis { margin: 0; font-size: 12px; line-height: 1.5; color: var(--muted); display: -webkit-box; -webkit-line-clamp: 3; line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
.rows { list-style: none; margin: 0; padding: 0; display: grid; gap: var(--space-3); }
.rows li { display: grid; gap: 4px; }
.top { display: flex; justify-content: space-between; gap: var(--space-2); font-size: 12px; color: var(--muted); }
.top b { color: var(--text); font-weight: 600; font-variant-numeric: tabular-nums; }
.skel { height: 160px; border-radius: var(--radius); background: var(--panel); }
</style>
