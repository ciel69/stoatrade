<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { PlanRow, actorKey, humanAction, planShort, useTradingStore, type DecisionItem } from '@/entities/trading'
import { UiChip } from '@/shared/ui'
import { fixed, price, signed, useNow } from '@/shared/lib'

/**
 * Боковая панель плана: тезис и условие входа, параметры, ревизии с различиями и хронология решений по плану, полный spec и runtime под сворачиванием.
 * Данные приходят по WebSocket по запросу (op plan); пока панель открыта, при изменении строки плана в списке запрашиваются заново (не чаще раза в 3 с). Только чтение.
 */
const props = defineProps<{ planId: string }>()
const emit = defineEmits<{ close: [] }>()
const trading = useTradingStore()
const now = useNow()
const panel = ref<HTMLElement | null>(null)

const row = computed(() => trading.plans?.items.find((p) => p.id === props.planId) ?? null)
const reply = computed(() => trading.planDetail[props.planId] ?? null)
const d = computed(() => (reply.value?.ok ? reply.value.data ?? null : null))
const card = computed(() => d.value?.card ?? null)
const failed = computed(() => (reply.value && !reply.value.ok ? reply.value.error ?? 'нет данных' : null))

let last = 0
let timer: ReturnType<typeof setTimeout> | undefined
function request(): void {
  clearTimeout(timer)
  const wait = Math.max(0, 3000 - (Date.now() - last))
  timer = setTimeout(() => { last = Date.now(); trading.requestPlan(props.planId) }, wait)
}
watch(() => props.planId, request, { immediate: true })
watch(() => JSON.stringify([row.value?.status, row.value?.rev, row.value?.col, row.value?.hold, row.value?.close_reason, row.value?.close_pnl]), request)
onUnmounted(() => clearTimeout(timer))

function onKey(ev: KeyboardEvent): void {
  if (ev.key === 'Escape') emit('close')
}
onMounted(() => {
  document.addEventListener('keydown', onKey)
  panel.value?.focus({ preventScroll: true })
})
onUnmounted(() => document.removeEventListener('keydown', onKey))

const STATUS_RU: Record<string, string> = { draft: 'черновик', armed: 'взведён', entered: 'вход исполнен', managing: 'сопровождается', closed: 'закрыт', cancelled: 'отменён', expired: 'истёк', invalidated: 'отозван', rejected: 'отклонён' }
const WARN_RU: Record<string, string> = { W_NO_INVALIDATION: 'нет условий отмены тезиса', W_LADDER_COLLAPSE: 'лесенка тейков вырождена', W_TTL_CLAMPED: 'срок обрезан по сессии', W_ADD_DISABLED: 'добор выключен', W_NO_RUNNER_RULE: 'остаток без трейла и срока', W_PRICE_STALE_AT_ACCEPT: 'цена была устаревшей при приёме', W_FIELD_IGNORED: 'лишнее поле проигнорировано' }
const dateFmt = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false })
const timeFmt = new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
const dt = (s: number | null | undefined) => (s != null ? dateFmt.format(new Date(s * 1000)) : '–')
const at = (x: DecisionItem) => (x.t != null ? timeFmt.format(new Date(x.t * 1000)) : x.ts.slice(11, 19))
const dateAt = (x: DecisionItem) => (x.t != null ? dateFmt.format(new Date(x.t * 1000)) : x.ts.slice(0, 16).replace('T', ' '))
const side = computed(() => (card.value?.side ?? row.value?.side) === 'long' ? 'лонг' : (card.value?.side ?? row.value?.side) === 'short' ? 'шорт' : '?')

const facts = computed<Array<[string, string]>>(() => {
  const c = card.value
  if (!c) return []
  const out: Array<[string, string | null]> = [
    ['Статус', `${STATUS_RU[c.status] ?? c.status}${c.hold ? ` · пауза${c.hold_reason ? ': ' + c.hold_reason : ''}` : ''} · ревизия ${c.rev}${c.rev_applied != null ? ` (применена ${c.rev_applied})` : ''}`],
    ['Сессия · создал', `${c.session_type ?? '–'} · ${c.created_by ?? '–'}`],
    ['Источник', c.src ? `${c.src}${c.parent ? ' от ' + planShort(c.parent) : ''}` : null],
    ['Создан', dt(c.ts_created)],
    ['Взведён', c.ts_armed != null ? dt(c.ts_armed) : null],
    ['Вход', c.ts_entered != null ? dt(c.ts_entered) : null],
    ['Закрыт', c.ts_closed != null ? `${dt(c.ts_closed)}${c.close_reason ? ' · ' + c.close_reason : ''}${c.close_pnl != null && c.status === 'closed' ? ' · ' + signed(c.close_pnl, 3) + ' USDT' : ''}` : null],
    ['Эксперимент', [c.exp, c.var, c.setup, c.hyp].filter(Boolean).join(' / ') || null],
    ['Риск · RR · плечо', [c.risk_usdt != null ? `${fixed(c.risk_usdt, 2)} USDT` : '', c.net_rr != null ? `RR ${fixed(c.net_rr, 1)}` : '', c.leverage != null ? `${c.leverage}×` : ''].filter(Boolean).join(' · ') || null],
    ['Стоп', c.stop != null ? price(c.stop) : null],
    ['Цели', c.targets.length ? c.targets.map((t) => `${t.id} ${price(t.price)}${t.state ? ' (' + t.state + ')' : ''}`).join(', ') : null],
  ]
  return out.filter((x): x is [string, string] => x[1] != null)
})
const showRow = computed(() => row.value != null && (row.value.col === 'waiting' || row.value.col === 'watching'))
const revs = computed(() => (d.value?.revs ?? []).slice().reverse())
const decisions = computed(() => (d.value?.decisions ?? []).slice(0, 40))
const json = ref(false)
const jsonText = computed(() => (json.value && d.value ? JSON.stringify({ spec: d.value.spec, runtime: d.value.runtime }, null, 2) : ''))
const resultText = (x: DecisionItem) => (x.denied ? 'отказ' : x.noop ? 'пропуск' : x.idle ? 'ждёт' : x.ok === true ? 'ок' : x.ok === false ? 'ошибка' : '')
</script>

<template>
  <aside ref="panel" class="panel" tabindex="-1" role="complementary" :aria-label="`План ${planShort(planId)}`">
    <header class="head">
      <div class="title">
        <b class="sym">{{ card?.sym ?? row?.sym ?? planShort(planId) }}</b>
        <UiChip>{{ side }}</UiChip>
        <UiChip v-if="card">{{ STATUS_RU[card.status] ?? card.status }}</UiChip>
        <span class="id">{{ planShort(planId) }}</span>
      </div>
      <button type="button" class="x" aria-label="Закрыть панель" @click="emit('close')">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>
      </button>
    </header>

    <div class="body">
      <ul v-if="showRow && row" class="viz"><PlanRow :plan="row" :now="now" /></ul>

      <p v-if="failed" class="note">Не удалось загрузить план: {{ failed }}.</p>
      <p v-else-if="!d" class="note" aria-busy="true">Загрузка…</p>
      <template v-else-if="card">
        <section v-if="card.thesis" class="blk">
          <h3>Тезис</h3>
          <p class="thesis">{{ card.thesis }}</p>
        </section>
        <section class="blk">
          <h3>Вход</h3>
          <p class="entry">{{ card.entry_text || '–' }}</p>
        </section>

        <dl class="kv">
          <template v-for="[k, v] in facts" :key="k"><dt>{{ k }}</dt><dd>{{ v }}</dd></template>
        </dl>
        <p v-if="card.warnings.length" class="warns">
          <UiChip v-for="w in card.warnings" :key="w" tone="warn" :title="w">{{ WARN_RU[w] ?? w }}</UiChip>
        </p>

        <section class="blk">
          <h3>Ревизии <span class="n">{{ revs.length }}</span></h3>
          <ul v-if="revs.length" class="revs">
            <li v-for="r in revs" :key="r.rev">
              <div class="rline"><b>r{{ r.rev }}</b><span class="muted">{{ r.ts.slice(0, 16).replace('T', ' ') }}{{ r.by ? ' · ' + r.by : '' }}</span></div>
              <p v-if="r.why" class="why">{{ r.why }}</p>
              <p v-for="(c, i) in r.changes" :key="i" class="diff">{{ c }}</p>
            </li>
          </ul>
          <p v-else class="muted">Ревизий нет.</p>
        </section>

        <section class="blk">
          <h3>Решения по плану <span class="n">{{ d.decisions.length }}</span></h3>
          <ul v-if="decisions.length" class="decs">
            <li v-for="x in decisions" :key="x.seq" :class="{ bad: x.denied || x.noop }">
              <span class="t" :title="dateAt(x)">{{ at(x) }}</span>
              <span class="what"><UiChip>{{ actorKey(x.actor) || '–' }}</UiChip> <b>{{ humanAction(x) }}</b><span v-if="x.why" class="muted"> · {{ x.why }}</span></span>
              <span v-if="resultText(x)" class="res">{{ resultText(x) }}</span>
            </li>
          </ul>
          <p v-else class="muted">Решений по этому плану пока нет.</p>
        </section>

        <details class="raw" @toggle="json = ($event.target as HTMLDetailsElement).open">
          <summary>Полный spec и состояние исполнителя (JSON)</summary>
          <pre v-if="json">{{ jsonText }}</pre>
        </details>
      </template>
    </div>
  </aside>
</template>

<style scoped>
.panel { display: flex; flex-direction: column; min-height: 0; background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); outline: none; }
.head { display: flex; align-items: center; justify-content: space-between; gap: var(--space-2); padding: var(--space-3) var(--space-4); border-bottom: 1px solid var(--line); }
.title { display: flex; align-items: center; gap: var(--space-2); min-width: 0; }
.sym { font-size: 17px; letter-spacing: -0.01em; }
.id { font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.x { flex: none; display: grid; place-items: center; width: 32px; height: 32px; padding: 0; background: transparent; border: 0; border-radius: var(--radius-sm); color: var(--muted); cursor: pointer; transition: color var(--dur-fast) ease; }
.x:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
@media (hover: hover) and (pointer: fine) { .x:hover { color: var(--text); } }
.body { flex: 1 1 auto; min-height: 0; overflow-y: auto; padding: var(--space-3) var(--space-4) var(--space-4); display: grid; gap: var(--space-4); align-content: start; }
.viz { list-style: none; margin: 0; padding: 0 0 var(--space-3); border-bottom: 1px solid var(--line); }
.viz :deep(li) { border-top: 0; padding: 0; }
.note { margin: 0; font-size: 13px; color: var(--muted); }
.blk h3 { margin: 0 0 var(--space-2); font-size: 12px; font-weight: 600; color: var(--muted); display: flex; align-items: baseline; gap: var(--space-2); }
.n { font-weight: 400; }
.thesis, .entry { margin: 0; font-size: 13px; line-height: 1.5; overflow-wrap: anywhere; }
.kv { margin: 0; display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 6px var(--space-3); font-size: 12px; }
.kv dt { color: var(--muted); }
.kv dd { margin: 0; overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }
.warns { margin: 0; display: flex; flex-wrap: wrap; gap: var(--space-2); }
.revs, .decs { list-style: none; margin: 0; padding: 0; display: grid; gap: var(--space-3); }
.rline { display: flex; align-items: baseline; gap: var(--space-2); font-size: 12px; }
.muted { color: var(--muted); }
.why { margin: 2px 0 0; font-size: 12px; overflow-wrap: anywhere; }
.diff { margin: 2px 0 0; padding-left: var(--space-2); border-left: 2px solid var(--line2); font-size: 11px; color: var(--muted); font-family: var(--mono, ui-monospace, monospace); overflow-wrap: anywhere; }
.decs li { display: grid; grid-template-columns: 62px minmax(0, 1fr) auto; gap: var(--space-2); align-items: baseline; font-size: 12px; }
.decs .t { color: var(--muted); font-variant-numeric: tabular-nums; }
.decs .what { overflow-wrap: anywhere; }
.decs .res { color: var(--muted); }
.decs li.bad .res { color: var(--warn); }
.raw summary { cursor: pointer; font-size: 12px; color: var(--muted); }
.raw pre { margin: var(--space-2) 0 0; max-height: 360px; overflow: auto; padding: var(--space-3); background: var(--panel2); border-radius: var(--radius-sm); font-size: 11px; line-height: 1.45; }
</style>
