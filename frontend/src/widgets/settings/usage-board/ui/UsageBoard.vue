<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { roleLabel, swapsLine, useRoleStore } from '@/entities/role'
import {
  CUTS, PERIODS, budgetCaption, budgetLevel, budgetTitle, costLine, groupToks, intText, isFreeLocal, isHigh, leftFrac, sortBudget, toksText, usageKey, useUsageStore, withShares,
  type UsageBy, type UsagePanelRef, type UsagePeriod,
} from '@/entities/usage'
import { useLive } from '@/shared/api'
import { UiMeter, UiSegment } from '@/shared/ui'
import { duration, fixed, useNow } from '@/shared/lib'

/**
 * Блок «Сколько стоит»: расход мозга за выбранный период в выбранном разрезе (по моделям, провайдерам, ролям). Данные по параметрам, поэтому запрашиваются по сокету
 * при заходе, при смене переключателя и раз в 30 секунд, пока блок на экране и вкладка видна; ответы хранятся по ключу «период:разрез», прежний остаётся на экране, пока идёт новый.
 * Сверху одна фраза с итогом и строка вызовов и токенов; потолки расхода свёрнуты в одну строку («меньше всего осталось 76%»), ниже первые пять строк «кто тратит»: имя, токены, деньги.
 * Качество, скорость и цена за миллион токенов открываются в боковой панели по клику на строку. Внизу ссылка на историю смен моделей (боковая панель). Только чтение.
 */
const props = defineProps<{ swapsOpen: boolean; selected: UsagePanelRef | null }>()
const emit = defineEmits<{ swaps: []; select: [id: string, days: UsagePeriod, by: UsageBy]; close: [] }>()
const store = useUsageStore()
const roles = useRoleStore()
const now = useNow()

// при открытии страницы по ссылке с панелью берём период и разрез из адреса, чтобы строка нашлась и подсветилась
const days = ref<UsagePeriod>(props.selected?.days ?? 1)
const by = ref<UsageBy>(props.selected?.by ?? 'model')
const isSel = (id: string): boolean => !!props.selected && props.selected.id === id && props.selected.days === days.value && props.selected.by === by.value
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
const swapsText = computed(() => swapsLine(roles.snapshot?.revisions ?? swaps.value.length))

// редкое по запросу: потолки расхода свёрнуты, в списке первые строки
const ceilings = ref(false)
/** Свёрнутая строка потолков: где осталось меньше всего, чтобы не раскрывать ради «всё в порядке». */
const ceilingLine = computed(() => {
  if (!budget.value.length) return ''
  const low = budget.value.reduce((a, b) => (leftFrac(b) < leftFrac(a) ? b : a))
  return `меньше всего осталось ${Math.round(leftFrac(low) * 100)}% («${budgetTitle(low).toLowerCase()}»)`
})
const TOP = 5
const allRows = ref(false)
const visible = computed(() => (allRows.value ? groups.value : groups.value.slice(0, TOP)))

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
watch(key, () => { if (props.selected) emit('close'); load() })
</script>

<template>
  <section class="board" aria-label="Сколько стоит">
    <header class="top">
      <h2 class="title">Сколько стоит</h2>
      <span class="state" aria-live="polite">{{ loading ? 'загрузка…' : refreshing ? 'обновляется…' : '' }}</span>
    </header>

    <UiSegment v-model="period" class="period" :options="PERIODS" label="Период" />

    <template v-if="data">
      <div class="sum">
        <p class="lead">{{ line }}</p>
        <p v-if="data.total.calls" class="detail">{{ intText(data.total.calls) }} вызовов мозга, {{ toksText(data.total.toks) }} токенов (единиц текста, за которые берут плату)<template v-if="days > 1">; из них сегодня ${{ (data.today.usd ?? 0) < 1 ? fixed(data.today.usd ?? 0, 4) : fixed(data.today.usd ?? 0, 2) }}</template>.</p>
      </div>

      <section v-if="budget.length" class="group">
        <button type="button" class="fold-btn" :aria-expanded="ceilings" aria-controls="ub-ceil" @click="ceilings = !ceilings">
          <span>Потолки расхода: {{ ceilingLine }}</span>
          <svg viewBox="0 0 12 12" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 4.5L6 7.5L9 4.5" /></svg>
        </button>
        <div id="ub-ceil" class="fold" :class="{ on: ceilings }">
          <ul class="budget" :inert="!ceilings">
            <li v-for="(b, i) in budget" :key="i">
              <div class="bh"><b>{{ budgetTitle(b) }}</b><span class="left">осталось {{ Math.round(leftFrac(b) * 100) }}%</span></div>
              <UiMeter :fill="leftFrac(b)" :level="budgetLevel(leftFrac(b))" :label="budgetTitle(b)" />
              <p class="cp">{{ budgetCaption(b, now, duration) }}</p>
            </li>
          </ul>
        </div>
      </section>

      <section class="group">
        <div class="cuthead">
          <h3 class="cap">Кто тратит</h3>
          <UiSegment v-model="by" :options="CUTS" label="Разрез расхода" />
        </div>
        <ul v-if="groups.length" class="rows">
          <li v-for="g in visible" :key="g.id">
            <button type="button" class="row" :class="{ sel: isSel(g.id) }" :aria-pressed="isSel(g.id)" @click="emit('select', g.id, days, by)">
              <span class="name">
                <b>{{ label(g.id) }}</b>
                <span v-if="isFreeLocal(g, by)" class="free">бесплатно, на этом компьютере</span>
                <span v-else-if="isHigh(g.err_rate, g.calls) || isHigh(g.fmt_err_rate, g.calls)" class="hot">много сбоев</span>
              </span>
              <span class="fig tk"><b>{{ toksText(groupToks(g)) }}</b><span>токенов</span></span>
              <span class="fig cs" :class="{ nil: !(g.usd ?? 0) }"><b>{{ (g.usd ?? 0) > 0 ? `$${(g.usd ?? 0) < 1 ? fixed(g.usd ?? 0, 4) : fixed(g.usd ?? 0, 2)}` : '$0' }}</b><span>{{ (g.usd ?? 0) > 0 ? `${Math.round(g.share * 100)}% расхода` : 'без затрат' }}</span></span>
              <svg class="chev" viewBox="0 0 12 12" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4.5 3L7.5 6L4.5 9" /></svg>
            </button>
          </li>
        </ul>
        <p v-else class="empty">За этот период вызовов не было.</p>
        <button v-if="groups.length > TOP" type="button" class="link" :aria-expanded="allRows" @click="allRows = !allRows">{{ allRows ? 'Показать меньше' : `Показать все (${groups.length})` }}</button>
      </section>
    </template>
    <p v-else-if="reply && !reply.ok" class="err">Не удалось получить расход: {{ reply.error ?? 'ошибка сервера' }}</p>
    <p v-else-if="loading" class="empty" aria-busy="true">Загружаем расход за выбранный период…</p>
    <p v-else class="empty">Расход появится, когда установится связь с сервером.</p>

    <button type="button" class="swaps" :class="{ sel: swapsOpen }" :aria-pressed="swapsOpen" @click="emit('swaps')">
      <span>История смен моделей</span>
      <span class="cnt">{{ swapsText }}</span>
      <svg class="chev" viewBox="0 0 12 12" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4.5 3L7.5 6L4.5 9" /></svg>
    </button>
  </section>
</template>

<style scoped>
.board { display: grid; gap: var(--space-4); }
.top { display: flex; align-items: baseline; gap: var(--space-3); }
.title { margin: 0; font-size: 16px; font-weight: 700; letter-spacing: -0.01em; margin-right: auto; }
.state { font-size: 11px; color: var(--muted); }
.period { justify-self: start; }
.sum { display: grid; gap: 2px; }
.lead { margin: 0; font-size: 15px; font-weight: 600; line-height: 1.4; }
.detail { margin: 0; font-size: 12px; color: var(--muted); line-height: 1.45; }
.group { display: grid; gap: var(--space-2); }
.cuthead { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: var(--space-2) var(--space-3); }
.cap { margin: 0; font-size: 12px; font-weight: 600; color: var(--muted); }
.fold-btn { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); width: 100%; min-height: 40px; padding: 0; background: none; border: 0; color: var(--muted); font: inherit; font-size: 13px; text-align: left; cursor: pointer; transition: color var(--dur-fast) ease; }
.fold-btn svg { flex: none; transition: transform var(--dur-enter) var(--ease-out); }
.fold-btn[aria-expanded='true'] svg { transform: rotate(180deg); }
.fold-btn:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 2px; }
.fold { display: grid; grid-template-rows: 0fr; transition: grid-template-rows var(--dur-enter) var(--ease-out); }
.fold.on { grid-template-rows: 1fr; }
.fold > .budget { overflow: hidden; min-height: 0; }
.budget { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--space-4); }
.fold.on > .budget { padding-top: var(--space-2); }
.bh { display: flex; justify-content: space-between; gap: var(--space-3); font-size: 13px; margin-bottom: 6px; }
.left { font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.cp { margin: 6px 0 0; font-size: 12px; color: var(--muted); }
.rows { list-style: none; margin: 0; padding: 0; background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); overflow: hidden; }
.rows li + li { border-top: 1px solid var(--line); }
.row { display: grid; grid-template-columns: minmax(0, 1fr) 110px 110px 12px; gap: var(--space-4); align-items: center; width: 100%; min-height: 56px; padding: var(--space-3) var(--space-4); background: none; border: 0; color: inherit; font: inherit; text-align: left; cursor: pointer; transition: background-color var(--dur-fast) ease; }
.row.sel { background: var(--panel2); }
.row:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.chev { color: var(--muted); }
.name { display: flex; flex-wrap: wrap; align-items: baseline; gap: 2px var(--space-3); font-size: 14px; min-width: 0; overflow-wrap: anywhere; }
.free { font-size: 12px; color: var(--muted); }
.hot { font-size: 12px; color: var(--warn); }
.fig { display: grid; gap: 1px; font-variant-numeric: tabular-nums; min-width: 0; }
.fig b { font-size: 15px; line-height: 1.2; font-weight: 650; }
.fig span { font-size: 11px; color: var(--muted); }
.fig.nil b { color: var(--muted); }
.empty { margin: 0; font-size: 13px; color: var(--muted); }
.err { margin: 0; font-size: 13px; color: var(--neg); }
.link { justify-self: start; padding: 4px 0; background: none; border: 0; color: var(--muted); font: inherit; font-size: 12px; text-decoration: underline; text-underline-offset: 3px; cursor: pointer; }
.link:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 2px; }
.swaps { display: flex; align-items: center; gap: var(--space-4); width: 100%; min-height: 44px; padding: 0 var(--space-1); background: none; border: 0; border-top: 1px solid var(--line); color: var(--muted); font: inherit; font-size: 13px; text-align: left; cursor: pointer; transition: color var(--dur-fast) ease; }
.swaps > span:first-child { margin-right: auto; }
.swaps.sel { color: var(--text); }
.swaps:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.cnt { font-size: 12px; }
@media (max-width: 900px) {
  .budget { grid-template-columns: minmax(0, 1fr); }
  .row { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) 12px; grid-template-areas: 'name name chev' 'tk cs cs'; row-gap: var(--space-1); }
  .row .name { grid-area: name; }
  .row .chev { grid-area: chev; }
  .row .fig.tk { grid-area: tk; }
  .row .fig.cs { grid-area: cs; }
}
@media (prefers-reduced-motion: reduce) {
  .fold, .fold-btn svg { transition: none; }
}
@media (hover: hover) and (pointer: fine) {
  .row:not(.sel):hover { background: var(--panel2); }
  .fold-btn:hover, .swaps:hover, .link:hover { color: var(--text); }
}
</style>
