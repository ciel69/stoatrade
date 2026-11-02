<script setup lang="ts">
import { computed } from 'vue'
import { expState, hiddenRows, rowViews, thresholdText, useExperimentStore } from '@/entities/experiment'
import { useTradingStore } from '@/entities/trading'
import { useLive } from '@/shared/api'
import { EXP_CUTS, TABS_FULL_TEXT, TABS_FULL_TITLE, cutInfo, modeName, type ExpCut } from '@/shared/lib'
import { UiChip, UiSegment } from '@/shared/ui'

/**
 * Таблица по выбранному разрезу сделок. Разрез выбирается сегментами: сверху «что проверяем» (гипотезы, эксперименты, варианты), ниже «по какому признаку сделки делятся».
 * Режим берётся из общего переключателя в шапке. Строки: подтверждённые сверху, затем группы с достаточным числом сделок по среднему результату, затем «мало данных».
 * Строка открывает боковую панель со сделками группы (выбор хранится в адресе страницы). На узком экране строка превращается в карточку с подписанными числами.
 */
const props = defineProps<{ by: ExpCut; selected: string | null }>()
const emit = defineEmits<{ 'update:by': [value: ExpCut]; select: [key: string] }>()

const store = useExperimentStore()
const trading = useTradingStore()
const { link, full } = useLive()
const d = computed(() => store.detail)
const state = computed(() => expState(store.detail, store.pending, link.value === 'open' || !!store.mocked))
const rows = computed(() => (state.value === 'ok' ? rowViews(d.value, props.by) : []))
const cut = computed(() => cutInfo(props.by))
const idea = EXP_CUTS.filter((c) => c.group === 'idea').map((c) => ({ value: c.value, label: c.label }))
const trade = EXP_CUTS.filter((c) => c.group === 'trade').map((c) => ({ value: c.value, label: c.label }))
const ideaSel = computed<ExpCut | ''>(() => (cut.value.group === 'idea' ? props.by : ''))
const tradeSel = computed<ExpCut | ''>(() => (cut.value.group === 'trade' ? props.by : ''))
const threshold = computed(() => thresholdText(d.value))
const hidden = computed(() => hiddenRows(d.value))
const modeLine = computed(() => modeName(trading.mode === 'all' ? null : trading.mode))
const unreviewed = computed(() => (props.by === 'err' ? (d.value?.unreviewed ?? 0) : 0))
</script>

<template>
  <section class="tbl" aria-labelledby="et-h">
    <header class="head">
      <h2 id="et-h" class="h">Результаты по группам</h2>
      <p class="mode">{{ trading.mode === 'all' ? 'Все режимы вместе, каждый отдельной строкой' : `Режим: ${modeLine}` }}. Меняется переключателем в шапке.</p>
    </header>

    <div class="cuts">
      <div class="cutrow">
        <span class="cap">Что проверяем</span>
        <UiSegment :model-value="ideaSel as ExpCut" :options="idea" label="Что проверяем" @update:model-value="(v) => emit('update:by', v)" />
      </div>
      <div class="cutrow">
        <span class="cap">По признаку сделки</span>
        <UiSegment :model-value="tradeSel as ExpCut" :options="trade" label="По признаку сделки" @update:model-value="(v) => emit('update:by', v)" />
      </div>
    </div>
    <p class="about">{{ cut.about }}</p>

    <Transition name="swap" mode="out-in">
      <div v-if="state === 'ok'" :key="`${by}|${trading.mode}`" class="body">
        <p v-if="threshold" class="thr">{{ threshold }}</p>
        <div class="cols" aria-hidden="true">
          <span>{{ cut.one }}</span><span class="r">Сделок</span><span class="r">Средний результат</span><span class="r">Прибыльных</span><span class="r">Итог, USDT</span><span>Насколько это случайно</span>
        </div>
        <ul class="rows">
          <li v-for="r in rows" :key="r.key">
            <button type="button" class="row" :aria-pressed="selected === r.key" @click="emit('select', r.key)">
              <span class="name">
                <b class="t">{{ r.title }}</b>
                <UiChip v-if="r.confirmed" tone="ok">подтверждена</UiChip>
                <UiChip v-else-if="r.small" tone="warn">мало данных</UiChip>
                <span v-if="r.mode" class="md">{{ r.mode }}</span>
                <span v-if="r.note" class="nt">{{ r.note }}</span>
              </span>
              <span class="c r" data-l="Сделок">{{ r.n }}</span>
              <span class="c r" :class="r.avgTone" data-l="Средний результат">{{ r.avgR }}</span>
              <span class="c r" data-l="Прибыльных">{{ r.win }}</span>
              <span class="c r" :class="r.pnlTone" data-l="Итог, USDT">{{ r.pnl }}</span>
              <span class="c p" :class="r.p.tone" :title="r.pHint" data-l="Насколько случайно">{{ r.p.text }}</span>
            </button>
          </li>
        </ul>
        <p v-if="hidden" class="note">Показаны {{ rows.length }} групп с наибольшим числом сделок, ещё {{ hidden }} не показаны.</p>
        <p v-if="unreviewed" class="note">Сделок без разбора: {{ unreviewed }}. Теги ошибок ставятся после закрытия сделки, пока их нет, эти сделки в таблицу не попадают.</p>
      </div>
      <p v-else-if="state === 'none'" key="none" class="empty">Пока нет закрытых сделок с экспериментами. Как только сделки закроются, здесь появятся группы с результатами.</p>
      <p v-else-if="state === 'cut'" key="cut" class="empty">В этом разрезе групп нет: у закрытых сделок нет нужной пометки. Выберите другой разрез.</p>
      <p v-else-if="state === 'failed'" key="failed" class="empty">Не удалось собрать статистику. Выберите другой разрез или зайдите позже.</p>
      <p v-else-if="state === 'link'" key="link" class="empty">{{ full ? `${TABS_FULL_TITLE}, данные не приходят. ${TABS_FULL_TEXT}` : 'Нет связи с платформой, данные не приходят. Страница переподключится сама.' }}</p>
      <div v-else key="load" class="skel" aria-busy="true" />
    </Transition>
  </section>
</template>

<style scoped>
.tbl { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: var(--space-4); min-width: 0; display: grid; gap: var(--space-3); align-content: start; }
.head { display: grid; gap: 2px; }
.h { margin: 0; font-size: 14px; font-weight: 600; }
.mode { margin: 0; font-size: 12px; color: var(--muted); }
.cuts { display: grid; gap: var(--space-2); }
.cutrow { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-1) var(--space-3); }
.cap { font-size: 12px; color: var(--muted); min-width: 132px; }
.about { margin: 0; font-size: 13px; line-height: 1.5; color: var(--muted); max-width: 78ch; text-wrap: pretty; }
.body { display: grid; gap: var(--space-2); min-width: 0; }
.thr { margin: 0; padding: var(--space-2) var(--space-3); background: var(--panel2); border-radius: var(--radius-sm); font-size: 12px; line-height: 1.5; color: var(--muted); text-wrap: pretty; }
.cols, .row { display: grid; grid-template-columns: minmax(180px, 3fr) 72px 120px 96px 96px minmax(170px, 2fr); gap: var(--space-3); align-items: baseline; }
.cols { padding: 0 var(--space-3); font-size: 11px; color: var(--muted); }
.r { text-align: right; font-variant-numeric: tabular-nums; }
.rows { list-style: none; margin: 0; padding: 0; display: grid; }
.rows li { border-top: 1px solid var(--line); }
.row { width: 100%; padding: var(--space-3); background: none; border: 0; border-radius: var(--radius-sm); color: inherit; font: inherit; font-size: 13px; text-align: left; cursor: pointer; transition: background-color var(--dur-fast) ease; }
.row[aria-pressed='true'] { background: var(--panel2); box-shadow: inset 0 0 0 1px var(--line2); }
.row:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.name { display: flex; flex-wrap: wrap; align-items: baseline; gap: var(--space-1) var(--space-2); min-width: 0; }
.t { font-weight: 600; overflow-wrap: anywhere; }
.md { font-size: 12px; color: var(--muted); }
.nt { flex-basis: 100%; font-size: 12px; line-height: 1.45; color: var(--muted); text-wrap: pretty; }
.c.pos { color: var(--pos); }
.c.neg { color: var(--neg); }
.p { color: var(--muted); font-size: 12px; line-height: 1.4; }
.p.ok { color: var(--pos); font-weight: 600; }
.p.warn { color: var(--warn); }
.note { margin: 0; font-size: 12px; line-height: 1.5; color: var(--muted); }
.empty { margin: 0; padding: var(--space-4) var(--space-2); font-size: 13px; line-height: 1.5; color: var(--muted); }
.skel { height: 220px; border-radius: var(--radius-sm); background: var(--panel2); }
@media (hover: hover) and (pointer: fine) {
  .row:not([aria-pressed='true']):hover { background: var(--panel2); }
}
/* средний экран: колонки уже, пояснение к оценке переносится */
@media (max-width: 1100px) {
  .cols, .row { grid-template-columns: minmax(140px, 3fr) 56px 100px 80px 80px minmax(120px, 2fr); gap: var(--space-2); }
}
/* узкий экран: строка это карточка, числа подписаны сами */
@media (max-width: 760px) {
  .cols { display: none; }
  .row { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--space-2) var(--space-4); padding: var(--space-3) var(--space-2); }
  .name { grid-column: 1 / -1; }
  .c { display: grid; gap: 1px; text-align: left; }
  .c::before { content: attr(data-l); font-size: 11px; font-weight: 400; color: var(--muted); }
  .c.p { grid-column: 1 / -1; }
  .cap { min-width: 0; flex-basis: 100%; }
}
.swap-enter-active { transition: opacity var(--dur-enter) var(--ease-out), transform var(--dur-enter) var(--ease-out); }
.swap-leave-active { transition: opacity 80ms linear; }
.swap-enter-from { opacity: 0; transform: translateY(4px); }
.swap-leave-to { opacity: 0; }
@media (prefers-reduced-motion: reduce) {
  .row, .swap-enter-active, .swap-leave-active { transition: none; }
  .swap-enter-from { transform: none; }
}
</style>
