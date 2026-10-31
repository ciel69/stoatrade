<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { pct, roleLabel, swapsLine, useRoleStore } from '@/entities/role'
import {
  CUTS, PERIODS, budgetCaption, budgetLevel, budgetTitle, costLine, intText, isFreeLocal, isHigh, leftFrac, msText, sortBudget, toksText, usageKey, useUsageStore, withShares,
  type UsageBy, type UsagePeriod,
} from '@/entities/usage'
import { useLive } from '@/shared/api'
import { UiMeter, UiSegment } from '@/shared/ui'
import { duration, fixed, useNow } from '@/shared/lib'

/**
 * Блок «Сколько стоит»: расход LLM за выбранный период в выбранном разрезе (по моделям, провайдерам, ролям). Данные по параметрам, поэтому запрашиваются по сокету
 * при заходе, при смене переключателя и раз в 30 секунд, пока блок на экране и вкладка видна; ответы хранятся по ключу «период:разрез», прежний остаётся на экране, пока идёт новый.
 * Сверху плитки итогов, шкалы дневного и часового потолка (сколько осталось, когда сбросится), ниже строки групп с долей стоимости и качеством.
 * Внизу ссылка на историю назначений (боковая панель). Только чтение.
 */
defineProps<{ swapsOpen: boolean }>()
const emit = defineEmits<{ swaps: [] }>()
const store = useUsageStore()
const roles = useRoleStore()
const now = useNow()

const days = ref<UsagePeriod>(1)
const by = ref<UsageBy>('model')
const key = computed(() => usageKey(days.value, by.value))
const reply = computed(() => store.replies[key.value] ?? null)
const loading = computed(() => !!store.pending[key.value] && !reply.value)
const refreshing = computed(() => !!store.pending[key.value] && !!reply.value)
const data = computed(() => (reply.value?.ok ? reply.value.data ?? null : null))
const groups = computed(() => withShares(data.value?.groups ?? []))
const budget = computed(() => sortBudget(data.value?.budget ?? []))
const label = (id: string): string => (by.value === 'role' ? roleLabel(id) : id)
const top = computed(() => (groups.value[0] ? { id: groups.value[0].id, share: groups.value[0].share } : null))
const line = computed(() => (data.value ? costLine(data.value.total.usd, data.value.total.calls, days.value, top.value, label) : ''))
const swaps = computed(() => roles.snapshot?.swaps ?? [])
const swapsText = computed(() => swapsLine(roles.snapshot?.revisions ?? swaps.value.length, roles.snapshot?.rev))
const updated = computed(() => {
  const t = store.receivedAt[key.value]
  return t ? Math.max(0, Math.floor(now.value - t)) : null
})

const period = computed({ get: () => String(days.value), set: (v: string) => { days.value = Number(v) as UsagePeriod } })
let timer: ReturnType<typeof setInterval> | null = null
let unState: (() => void) | null = null
function load(): void {
  store.request(days.value, by.value)
}
/** Повтор по таймеру только пока вкладка видна: в фоне запросы не шлём. */
function tick(): void {
  if (typeof document === 'undefined' || document.visibilityState === 'visible') load()
}
function onVisible(): void {
  if (document.visibilityState === 'visible') load()
}
onMounted(() => {
  store.start()
  const { socket } = useLive()
  // первый запрос после открытия сокета (или сразу, если он уже открыт), и повторный запрос после переподключения
  unState = socket.onState((s) => { if (s === 'open') load() })
  timer = setInterval(tick, 30000)
  document.addEventListener('visibilitychange', onVisible)
})
onUnmounted(() => {
  if (timer) clearInterval(timer)
  unState?.()
  document.removeEventListener('visibilitychange', onVisible)
})
watch(key, load)
</script>

<template>
  <section class="board" aria-label="Сколько стоит">
    <header class="top">
      <h2 class="title">Сколько стоит</h2>
      <span class="state" aria-live="polite">{{ loading ? 'загрузка...' : refreshing ? 'обновляется...' : updated != null ? `обновлено ${updated} с назад` : '' }}</span>
    </header>

    <div class="tools">
      <UiSegment v-model="period" :options="PERIODS" label="Период" />
      <UiSegment v-model="by" :options="CUTS" label="Разрез" />
    </div>

    <template v-if="data">
      <div class="sum"><p class="lead">{{ line }}</p></div>

      <div class="tiles">
        <div class="tile"><span class="k">Стоимость</span><b class="v">${{ (data.total.usd ?? 0) < 1 ? fixed(data.total.usd ?? 0, 4) : fixed(data.total.usd ?? 0, 2) }}</b></div>
        <div class="tile"><span class="k">Вызовов</span><b class="v">{{ intText(data.total.calls) }}</b></div>
        <div class="tile"><span class="k">Токенов</span><b class="v">{{ toksText(data.total.toks) }}</b></div>
        <div class="tile"><span class="k">Из них сегодня</span><b class="v">${{ (data.today.usd ?? 0) < 1 ? fixed(data.today.usd ?? 0, 4) : fixed(data.today.usd ?? 0, 2) }}</b><span class="sub">{{ intText(data.today.calls) }} вызовов, {{ toksText(data.today.toks) }} токенов</span></div>
      </div>

      <section v-if="budget.length" class="group">
        <h3 class="cap">Потолки расхода: сколько осталось</h3>
        <ul class="budget">
          <li v-for="(b, i) in budget" :key="i">
            <div class="bh"><b>{{ budgetTitle(b) }}</b><span class="left">осталось {{ Math.round(leftFrac(b) * 100) }}%</span></div>
            <UiMeter :fill="leftFrac(b)" :level="budgetLevel(leftFrac(b))" :label="budgetTitle(b)" />
            <p class="cp">{{ budgetCaption(b, now, duration) }}</p>
          </li>
        </ul>
      </section>

      <section class="group">
        <h3 class="cap">{{ by === 'model' ? 'По моделям' : by === 'provider' ? 'По провайдерам' : 'По ролям' }}</h3>
        <ul v-if="groups.length" class="rows">
          <li v-for="g in groups" :key="g.id" class="row">
            <div class="name">
              <b>{{ label(g.id) }}</b>
              <span v-if="isFreeLocal(g, by)" class="free">бесплатно, локально</span>
              <i class="share" :title="`Доля стоимости: ${Math.round(g.share * 100)}%`"><i class="sf" :style="{ transform: `scaleX(${g.share})` }" /></i>
            </div>
            <span class="cell cost"><b>${{ (g.usd ?? 0) < 1 ? fixed(g.usd ?? 0, 4) : fixed(g.usd ?? 0, 2) }}</b><small>{{ Math.round(g.share * 100) }}% стоимости</small></span>
            <span class="cell"><b>{{ intText(g.calls) }}</b><small>вызовов</small></span>
            <span class="cell" :class="{ hot: isHigh(g.err_rate, g.calls) }"><b>{{ pct(g.err_rate) }}</b><small>ошибки</small></span>
            <span class="cell" :class="{ hot: isHigh(g.fmt_err_rate, g.calls) }"><b>{{ pct(g.fmt_err_rate) }}</b><small>неверный формат</small></span>
            <span class="cell"><b>{{ pct(g.cache_hit_rate) }}</b><small>из кэша</small></span>
            <span class="cell"><b>{{ msText(g.lat_p50_ms) }}</b><small>типичный ответ</small></span>
          </li>
        </ul>
        <p v-else class="empty">За этот период вызовов не было.</p>
      </section>
    </template>
    <p v-else-if="reply && !reply.ok" class="err">Не удалось получить расход: {{ reply.error ?? 'ошибка сервера' }}</p>
    <p v-else-if="loading" class="empty" aria-busy="true">Загружаем расход за выбранный период...</p>
    <p v-else class="empty">Расход появится, когда установится связь с сервером.</p>

    <button type="button" class="swaps" :class="{ sel: swapsOpen }" :aria-pressed="swapsOpen" @click="emit('swaps')">
      <span><b>История назначений</b> <span class="muted">кто и когда менял модели ролей</span></span>
      <span class="cnt">{{ swapsText }}</span>
      <svg class="chev" viewBox="0 0 12 12" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4.5 3L7.5 6L4.5 9" /></svg>
    </button>
  </section>
</template>

<style scoped>
.board { display: grid; gap: var(--space-4); }
.top { display: flex; align-items: baseline; gap: var(--space-3); }
.title { margin: 0; font-size: 16px; font-weight: 700; letter-spacing: -0.01em; margin-right: auto; }
.state { font-size: 11px; color: var(--muted); font-variant-numeric: tabular-nums; }
.tools { display: flex; flex-wrap: wrap; gap: var(--space-3); }
.sum { padding: var(--space-4) var(--space-5); background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); }
.lead { margin: 0; font-size: 16px; font-weight: 600; }
.tiles { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: var(--space-3); }
.tile { display: grid; gap: 2px; align-content: start; padding: var(--space-3) var(--space-4); background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); }
.tile .k { font-size: 12px; color: var(--muted); }
.tile .v { font-size: 20px; font-variant-numeric: tabular-nums; }
.tile .sub { font-size: 11px; color: var(--muted); }
.group { display: grid; gap: var(--space-2); }
.cap { margin: 0; font-size: 12px; font-weight: 600; color: var(--muted); }
.budget { list-style: none; margin: 0; padding: var(--space-3) var(--space-4); display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--space-4); background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); }
.bh { display: flex; justify-content: space-between; gap: var(--space-3); font-size: 13px; margin-bottom: 6px; }
.left { font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.cp { margin: 6px 0 0; font-size: 12px; color: var(--muted); }
.rows { list-style: none; margin: 0; padding: 0; background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); overflow: hidden; }
.row { display: grid; grid-template-columns: minmax(0, 1.6fr) repeat(5, minmax(0, 0.7fr)) minmax(0, 0.8fr); gap: var(--space-3); align-items: start; padding: var(--space-3) var(--space-4); }
.row + .row { border-top: 1px solid var(--line); }
.name { display: grid; gap: 4px; font-size: 14px; min-width: 0; overflow-wrap: anywhere; }
.free { font-size: 12px; color: var(--muted); }
.share { display: block; height: 4px; border-radius: 99px; background: var(--panel2); overflow: hidden; }
.sf { display: block; height: 100%; width: 100%; transform-origin: left; background: var(--accent); border-radius: 99px; transition: transform 300ms var(--ease-out); }
.cell { display: grid; gap: 1px; font-size: 13px; font-variant-numeric: tabular-nums; }
.cell small { font-size: 11px; color: var(--muted); }
.cell.hot b { color: var(--warn); }
.empty { margin: 0; font-size: 13px; color: var(--muted); }
.err { margin: 0; font-size: 13px; color: var(--neg); }
.swaps { display: flex; align-items: center; gap: var(--space-4); width: 100%; padding: var(--space-3) var(--space-4); background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); color: inherit; font: inherit; font-size: 13px; text-align: left; cursor: pointer; transition: background-color var(--dur-fast) ease; }
.swaps > span:first-child { margin-right: auto; }
.swaps.sel { background: var(--panel2); }
.swaps:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.muted { color: var(--muted); font-size: 12px; margin-left: var(--space-2); }
.cnt { font-size: 12px; color: var(--muted); }
.chev { color: var(--muted); }
@media (max-width: 900px) {
  .tiles { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .budget { grid-template-columns: minmax(0, 1fr); }
  .row { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .row .name { grid-column: 1 / -1; }
}
@media (prefers-reduced-motion: reduce) {
  .sf { transition: none; }
}
@media (hover: hover) and (pointer: fine) {
  .swaps:not(.sel):hover { background: var(--panel2); }
}
</style>
