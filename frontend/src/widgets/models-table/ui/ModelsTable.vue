<script setup lang="ts">
import { computed, ref } from 'vue'
import { useTradingStore, type ModelRow } from '@/entities/trading'
import { UiChip, UiSegment } from '@/shared/ui'
import { signed } from '@/shared/lib'

/** Результат по моделям: сделки, которые принесли планы модели (тег model.*), против расхода на её вызовы. Режим берётся из шапки. До n=30 вывод ненадёжен, строка помечена. */
type Sort = 'net' | 'pnl' | 'eff' | 'usd'
const trading = useTradingStore()
const period = ref<'1' | '7' | '30'>('7')
const sort = ref<Sort>('net')
const PERIODS = [{ value: '1', label: 'Сутки' }, { value: '7', label: '7 дней' }, { value: '30', label: '30 дней' }] as const
const SORTS = [{ value: 'net', label: 'Чистый' }, { value: 'pnl', label: 'PnL' }, { value: 'eff', label: 'PnL / 100 вызовов' }, { value: 'usd', label: 'Расход' }] as const

const data = computed(() => trading.models?.periods[period.value] ?? null)
const key: Record<Sort, (r: ModelRow) => number> = {
  net: (r) => (r.n ? r.net : -1e9),
  pnl: (r) => (r.n ? r.pnl : -1e9),
  eff: (r) => r.pnl_per_100_calls ?? -1e9,
  usd: (r) => r.usd,
}
const rows = computed(() => [...(data.value?.rows ?? [])].sort((a, b) => key[sort.value](b) - key[sort.value](a)))
const max = computed(() => Math.max(1e-9, ...rows.value.map((r) => Math.abs(r.pnl))))
const best = computed(() => rows.value.filter((r) => r.n > 0).sort((a, b) => b.net - a.net)[0] ?? null)
const tone = (x: number | null) => ((x ?? 0) > 0 ? 'up' : (x ?? 0) < 0 ? 'down' : '')
const usd = (x: number) => (x > 0 ? `$${x.toFixed(3)}` : '0')
const pc = (x: number | null) => (x == null ? '–' : `${Math.round(x * 100)}%`)
</script>

<template>
  <section class="models" aria-label="Результат по моделям">
    <header class="head">
      <h2 class="title">Результат по моделям</h2>
      <UiSegment v-model="period" :options="PERIODS" label="Период" class="seg" />
    </header>

    <p v-if="best" class="lead">{{ best.net > 0 ? 'Больше всего принесла' : 'Лучший результат, но все в минусе, у' }} <b>{{ best.id }}</b>: <span :class="tone(best.net)">{{ signed(best.net) }} USDT</span> за вычетом расхода на ИИ, {{ best.n }} сделок<template v-if="!best.reliable"> · мало данных</template>.</p>
    <p v-else-if="data" class="lead muted">За период нет закрытых сделок с привязкой к модели, сравнивать пока нечего.</p>

    <div v-if="rows.length" class="sorts"><span class="muted">Сортировка</span><UiSegment v-model="sort" :options="SORTS" label="Сортировка" /></div>

    <div v-if="rows.length" class="wrap">
      <table>
        <thead>
          <tr>
            <th scope="col">Модель</th><th scope="col" class="n" title="Закрытых сделок по планам модели; в скобках доля прибыльных">Сделок</th><th scope="col" class="n" title="Результат закрытых сделок до расхода на ИИ">PnL, USDT</th>
            <th scope="col" class="n" title="Сколько стоили вызовы модели за период (локальная модель: 0)">Расход ИИ</th><th scope="col" class="n" title="PnL минус расход на ИИ: то, что модель реально принесла">Чистый</th>
            <th scope="col" class="n">Вызовов</th><th scope="col" class="n" title="Сколько USDT приходится на 100 вызовов">PnL / 100</th><th scope="col" class="n" title="Доля неудачных вызовов · доля ошибок формата">Ошибки</th>
            <th scope="col" class="n" title="Медианное время ответа">Ответ, с</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.id">
            <td class="m"><b>{{ r.id }}</b><UiChip v-if="r.n && !r.reliable" tone="warn">мало данных</UiChip></td>
            <td class="n">{{ r.n ? `${r.n} (${Math.round((r.wins / r.n) * 100)}%)` : '0' }}</td>
            <td class="n" :class="tone(r.pnl)">
              {{ r.n ? signed(r.pnl) : '–' }}
              <i v-if="r.n" class="bar" :class="r.pnl >= 0 ? 'pos' : 'neg'" :style="{ width: Math.max(3, (Math.abs(r.pnl) / max) * 100) + '%' }" />
            </td>
            <td class="n">{{ usd(r.usd) }}</td>
            <td class="n" :class="tone(r.n ? r.net : null)">{{ r.n ? signed(r.net) : '–' }}</td>
            <td class="n">{{ r.calls }}</td>
            <td class="n" :class="tone(r.pnl_per_100_calls)">{{ signed(r.pnl_per_100_calls) }}</td>
            <td class="n">{{ r.err_rate != null ? `${pc(r.err_rate)} · ${pc(r.fmt_err_rate ?? 0)}` : '–' }}</td>
            <td class="n">{{ r.lat_p50_ms != null ? (r.lat_p50_ms / 1000).toFixed(1) : '–' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-else-if="!trading.models" class="muted" aria-busy="true">Загрузка…</p>
    <p v-if="data" class="note">{{ data.note }}</p>
  </section>
</template>

<style scoped>
.models { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: var(--space-4); min-width: 0; }
.head { display: flex; align-items: center; gap: var(--space-3); margin-bottom: var(--space-3); flex-wrap: wrap; }
.title { margin: 0; font-size: 14px; font-weight: 600; }
.seg { margin-left: auto; }
.lead { margin: 0 0 var(--space-3); font-size: 13px; }
.muted { color: var(--muted); }
.up { color: var(--pos); }
.down { color: var(--neg); }
.sorts { display: flex; align-items: center; gap: var(--space-3); margin-bottom: var(--space-3); font-size: 12px; flex-wrap: wrap; }
.wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 12px; font-variant-numeric: tabular-nums; }
th, td { padding: 8px var(--space-2); border-top: 1px solid var(--line); text-align: left; white-space: nowrap; font-weight: 400; }
th { color: var(--muted); border-top: 0; cursor: help; }
td.m { display: flex; align-items: center; gap: var(--space-2); }
.n { text-align: right; }
td { position: relative; }
.bar { position: absolute; left: var(--space-2); bottom: 3px; height: 2px; max-width: calc(100% - 2 * var(--space-2)); border-radius: 2px; opacity: 0.7; }
.bar.pos { background: var(--pos); }
.bar.neg { background: var(--neg); }
.note { margin: var(--space-3) 0 0; font-size: 11px; color: var(--muted); line-height: 1.4; }
</style>
