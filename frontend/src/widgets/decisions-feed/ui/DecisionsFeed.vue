<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { actorKey, filterDecisions, humanAction, planShort, useTradingStore, type ActorFilter, type DecisionItem } from '@/entities/trading'
import { UiChip, UiSegment } from '@/shared/ui'
import { decisionReason, price, signed } from '@/shared/lib'

/** Лента решений: кто решил, что сделал, почему и чем кончилось. Фильтры: исполнитель, только отказы, план, поиск по тексту. Строка выбирается: параметры, основание, ответ и идентификаторы показывает страница боковой панелью. */
defineProps<{ selected: number | null }>()
const emit = defineEmits<{ select: [seq: number] }>()
const trading = useTradingStore()
const FIRST = 40
const STEP = 60
const actor = ref<ActorFilter>('all')
const onlyFail = ref<'all' | 'fail'>('all')
const plan = ref('')
const q = ref('')
const shown = ref(FIRST)
watch([actor, onlyFail, plan, q], () => { shown.value = FIRST })

const ACTORS = [{ value: 'all', label: 'Все' }, { value: 'brain', label: 'Мозг' }, { value: 'exec', label: 'Исполнитель' }, { value: 'owner', label: 'Владелец' }, { value: 'system', label: 'Система' }] as const
const FAIL = [{ value: 'all', label: 'Всё' }, { value: 'fail', label: 'Только отказы' }] as const
const ACTOR_RU: Record<string, string> = { brain: 'мозг', exec: 'исполнитель', owner: 'владелец', system: 'система', rule: 'правило' }
const KIND_RU: Record<string, string> = { cmd: 'команда', accept: 'принято', reject: 'отказ', transition: 'переход', intent: 'намерение', result: 'результат', rule: 'правило', skip: 'пропуск', protect: 'защита', reconcile: 'сверка', event: 'событие', note: 'заметка' }

const snap = computed(() => trading.decisions)
const filtered = computed(() => filterDecisions(snap.value?.items ?? [], { actor: actor.value, onlyFail: onlyFail.value === 'fail', plan: plan.value, q: q.value }))
const visible = computed(() => filtered.value.slice(0, shown.value))

const timeFmt = new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
const at = (d: DecisionItem, f: Intl.DateTimeFormat) => (d.t != null ? f.format(new Date(d.t * 1000)) : d.ts.slice(11, 19))

function result(d: DecisionItem): { text: string; tone: 'ok' | 'bad' | 'warn' | 'neutral' } {
  if (d.denied) return { text: d.code ? `отказ: ${decisionReason(d.code)}` : 'отказ', tone: 'bad' }
  if (d.idle) return { text: 'ждёт', tone: 'neutral' }
  if (d.noop) return { text: d.code ? `пропуск: ${decisionReason(d.code)}` : 'пропуск', tone: 'warn' }
  if (d.ok === true) return { text: 'выполнено', tone: 'ok' }
  return { text: KIND_RU[d.kind] ?? d.kind, tone: 'neutral' }
}
</script>

<template>
  <section class="feed" aria-label="Лента решений">
    <header class="head">
      <h2 class="title">Лента решений<span v-if="snap" class="n">{{ filtered.length === snap.items.length ? snap.total : `${filtered.length} из ${snap.items.length}` }}</span></h2>
    </header>

    <div class="filters">
      <UiSegment v-model="actor" :options="ACTORS" label="Кто принял решение" />
      <UiSegment v-model="onlyFail" :options="FAIL" label="Что показывать" />
      <select v-model="plan" class="field" aria-label="План">
        <option value="">Все планы</option>
        <option v-for="p in snap?.plans ?? []" :key="p" :value="p">{{ planShort(p) }}</option>
      </select>
      <input v-model="q" class="field search" type="search" placeholder="Поиск по тексту" aria-label="Поиск по тексту" />
    </div>

    <ul v-if="visible.length" class="list">
      <li v-for="d in visible" :key="d.seq" class="row" :class="{ bad: d.denied }">
        <button type="button" class="line" :aria-pressed="selected === d.seq" @click="emit('select', d.seq)">
          <span class="time">{{ at(d, timeFmt) }}</span>
          <span class="body">
            <span class="top">
              <UiChip :tone="actorKey(d.actor) === 'brain' ? 'accent' : 'neutral'">{{ ACTOR_RU[actorKey(d.actor)] ?? d.actor }}</UiChip>
              <span v-if="d.plan" class="plan" :title="d.plan">{{ planShort(d.plan) }}</span>
              <UiChip v-if="d.mode === 'paper'">paper</UiChip>
              <b class="action">{{ humanAction(d) }}</b>
              <span class="res"><UiChip :tone="result(d).tone">{{ result(d).text }}</UiChip><span v-if="d.px != null" class="px">{{ price(d.px) }}<template v-if="d.r != null"> · {{ signed(d.r) }}R</template></span></span>
            </span>
            <span v-if="d.why" class="why">{{ d.why }}</span>
          </span>
        </button>
      </li>
    </ul>
    <p v-else-if="snap" class="empty">{{ snap.items.length ? 'Нет решений по этим фильтрам.' : 'Решений пока нет.' }}</p>
    <div v-else class="skel" aria-busy="true" />

    <div v-if="filtered.length > FIRST" class="more-row">
      <button v-if="shown < filtered.length" type="button" class="btn" @click="shown += STEP">Показать ещё {{ Math.min(STEP, filtered.length - shown) }}</button>
      <button v-else type="button" class="btn" @click="shown = FIRST">Свернуть до {{ FIRST }}</button>
    </div>
  </section>
</template>

<style scoped>
.feed { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: var(--space-4); min-width: 0; }
.head { margin-bottom: var(--space-3); }
.title { margin: 0; font-size: 14px; font-weight: 600; display: flex; align-items: baseline; gap: var(--space-2); }
.n { font-size: 12px; font-weight: 400; color: var(--muted); }
.filters { display: flex; flex-wrap: wrap; gap: var(--space-2) var(--space-3); margin-bottom: var(--space-3); }
.field { height: 28px; padding: 0 var(--space-3); background: var(--panel2); color: var(--text); border: 1px solid var(--line); border-radius: var(--radius-sm); font: inherit; font-size: 12px; }
.field:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.search { min-width: 180px; flex: 1 1 180px; max-width: 280px; }
/* строки идут сеткой: на широком экране 2-3 колонки по 520 px; разделитель снизу, нижняя линия последнего ряда срезана clip-path */
.list { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(min(520px, 100%), 1fr)); column-gap: var(--space-5); clip-path: inset(0 0 1px 0); }
.row { border-bottom: 1px solid var(--line); }
.line { display: grid; grid-template-columns: 62px minmax(0, 1fr); column-gap: var(--space-3); width: 100%; padding: var(--space-2) var(--space-2); background: transparent; border: 0; border-radius: var(--radius-sm); color: inherit; font: inherit; font-size: 12px; text-align: left; cursor: pointer; transition: background-color var(--dur-fast) ease; }
.line[aria-pressed='true'] { background: var(--panel2); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent) 45%, transparent); }
@media (hover: hover) and (pointer: fine) {
  .line:hover { background: var(--panel2); }
}
.line:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.time { color: var(--muted); font-variant-numeric: tabular-nums; padding-top: 2px; }
.body { min-width: 0; display: grid; gap: 3px; }
.top { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-2); min-width: 0; }
.plan { color: var(--muted); font-size: 11px; }
.action { font-size: 13px; overflow-wrap: anywhere; }
.res { margin-left: auto; display: inline-flex; align-items: center; gap: var(--space-2); }
.px { color: var(--muted); font-variant-numeric: tabular-nums; }
.why { color: var(--muted); line-height: 1.4; overflow-wrap: anywhere; display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.empty { margin: 0; padding: var(--space-5) 0; text-align: center; color: var(--muted); font-size: 13px; }
.skel { height: 160px; border-radius: var(--radius-sm); background: var(--panel2); }
.more-row { display: flex; justify-content: center; margin-top: var(--space-4); }
.btn { padding: 6px 16px; background: var(--panel2); color: var(--text); border: 1px solid var(--line2); border-radius: var(--radius-sm); font-size: 12px; cursor: pointer; transition: border-color var(--dur-fast) ease; }
@media (hover: hover) and (pointer: fine) {
  .btn:hover { border-color: var(--muted); }
}
</style>
