<script setup lang="ts">
import { computed, ref, watchEffect } from 'vue'
import {
  bookKindWord, exchangesText, ladderSourceWord, musd, pct, priceText, spreadText, venueName, warnInfo,
  type CmktDetail, type CmktLadderKind, type CmktLevel,
} from '@/entities/cmkt'
import type { LinkState } from '@/shared/api'
import { UiChip, UiTip } from '@/shared/ui'

/**
 * Сводный стакан выбранной монеты: полосы глубины всех бирж вокруг середины (аски сверху, биды снизу) с ценой края полосы,
 * объёмом и числом бирж-источников; сверху лучшие цены и спред. Числа только из свежего ответа.
 */
const props = defineProps<{ sym: string | null; detail: CmktDetail | null; loading: boolean; link: LinkState }>()

const kind = ref<'perp' | 'spot'>('perp')
const touched = ref(false)
const ladder = computed(() => props.detail?.ladder ?? null)
const has = (k: 'perp' | 'spot'): boolean => !!ladder.value?.kinds[k]
function choose(k: 'perp' | 'spot'): void {
  touched.value = true
  kind.value = k
}
watchEffect(() => {
  if (touched.value) {
    if (!has(kind.value)) kind.value = has('perp') ? 'perp' : 'spot'
    return
  }
  if (has('perp')) kind.value = 'perp'
  else if (has('spot')) kind.value = 'spot'
})
const kinds = computed(() => (['perp', 'spot'] as const).filter(has))
const book = computed<CmktLadderKind | null>(() => ladder.value?.kinds[kind.value] ?? null)

const notFocus = computed(() => (props.detail?.warn ?? []).includes('NOT_FOCUS'))
const stale = computed(() => (props.detail?.warn ?? []).includes('STALE'))
const warnCodes = computed(() => (props.detail?.warn ?? []).filter((w) => w !== 'NOT_FOCUS' && w !== 'STALE').map(warnInfo))
const own = computed(() => (props.detail && props.sym && props.detail.sym?.toUpperCase() === props.sym.toUpperCase() ? props.detail : null))

const levelsOf = (side: 'ask' | 'bid'): CmktLevel[] => {
  const b = book.value
  if (!b) return []
  return b.walls[side].length ? b.walls[side] : b.levels[side]
}
const ladderWord = computed(() => {
  const b = book.value
  if (!b) return ''
  return b.walls.ask.length || b.walls.bid.length ? 'крупные уровни' : ladderSourceWord(b.source)
})
const asks = computed(() => levelsOf('ask'))
const bids = computed(() => levelsOf('bid'))
const asksTop = computed(() => [...asks.value].reverse())
const maxSize = computed(() => Math.max(1, ...asks.value.map((x) => x.musd), ...bids.value.map((x) => x.musd)))
const width = (lv: CmktLevel): string => `${Math.max(2, Math.round((lv.musd / maxSize.value) * 100))}%`
const srcText = (mkts: string[]): string => (mkts.length === 1 ? venueName(mkts[0]) : exchangesText(mkts.length))
const distPct = (lv: CmktLevel): number | null => {
  const mid = book.value?.mid
  if (lv.px === null || mid == null || mid === 0) return lv.pct
  return Math.abs((lv.px - mid) / mid) * 100
}
</script>

<template>
  <section class="book" aria-label="Сводный стакан">
    <header class="head">
      <div class="titles">
        <h3 class="h">Стакан</h3>
        <p v-if="book" class="sub">{{ bookKindWord(kind) }} · {{ book.n_mkts }} {{ book.n_mkts === 1 ? 'биржа' : 'биржи' }} · {{ ladderWord }}</p>
      </div>
      <div v-if="kinds.length > 1" class="kinds" role="group" aria-label="Рынок стакана">
        <button v-for="k in kinds" :key="k" type="button" :aria-pressed="kind === k" @click="choose(k)">{{ bookKindWord(k) }}</button>
      </div>
      <UiTip v-for="w in warnCodes" :key="w.word" :tip="{ title: w.word, text: w.about }">
        <UiChip :tone="w.tone">{{ w.word }}</UiChip>
      </UiTip>
    </header>

    <p v-if="!sym" class="state">Выберите монету — покажем её сводный стакан и глубину.</p>
    <p v-else-if="notFocus" class="state">Монета вне фокуса: сводка по ней сейчас не собирается.</p>
    <p v-else-if="stale" class="state warn">Данные по монете устарели: числа скрыты до свежего снимка.</p>
    <p v-else-if="!own && link !== 'open'" class="state">Нет связи с платформой: стакан придёт, когда соединение восстановится.</p>
    <p v-else-if="!own && loading" class="state" aria-busy="true">Собираю стакан…</p>
    <p v-else-if="!own" class="state">Стакан не пришёл. Выберите монету ещё раз.</p>
    <p v-else-if="!book" class="state">У бирж нет глубины по этой монете: ни уровней, ни полос.</p>

    <template v-else>
      <div class="ladder" role="table" aria-label="Уровни стакана">
        <div class="row head-row" role="row">
          <span role="columnheader">Цена</span>
          <span role="columnheader" class="num">Объём</span>
          <span role="columnheader" class="num">Источники</span>
        </div>
        <div v-for="lv in asksTop" :key="`a-${lv.px}-${lv.pct}`" class="row ask" role="row">
          <span class="fill" aria-hidden="true" :style="{ width: width(lv) }" />
          <span role="cell" class="px">{{ priceText(lv.px) }}<small v-if="lv.pct !== null">{{ pct(distPct(lv), (distPct(lv) ?? 0) < 1 ? 2 : 1) }} от середины</small></span>
          <span role="cell" class="num sz">{{ musd(lv.musd) }}</span>
          <span role="cell" class="num ex">{{ srcText(lv.mkts) }}</span>
        </div>
        <div class="row mid" role="row">
          <span role="cell" class="px">середина</span>
          <span role="cell" class="num sz">{{ priceText(book.mid) }}</span>
          <span role="cell" class="num ex">спред {{ spreadText(book.spread_pct) }}</span>
        </div>
        <div v-for="lv in bids" :key="`b-${lv.px}-${lv.pct}`" class="row bid" role="row">
          <span class="fill" aria-hidden="true" :style="{ width: width(lv) }" />
          <span role="cell" class="px">{{ priceText(lv.px) }}<small v-if="lv.pct !== null">{{ pct(distPct(lv), (distPct(lv) ?? 0) < 1 ? 2 : 1) }} от середины</small></span>
          <span role="cell" class="num sz">{{ musd(lv.musd) }}</span>
          <span role="cell" class="num ex">{{ srcText(lv.mkts) }}</span>
        </div>
      </div>

      <p v-if="(detail?.trunc?.length ?? 0) > 0" class="foot">Часть ответа не поместилась: {{ detail?.trunc?.join(', ') }}.</p>
      <p class="foot">Уровни — крупные заявки всех бирж; объём — сумма бирж-источников. Если крупных уровней нет, показаны полосы глубины.</p>
      <p v-if="(book.spread_pct ?? 0) <= 0" class="foot">Лучшие цены на разных биржах пересекаются (задержка или базис) — спред по ним не считается; уровни взяты из общего снимка.</p>
    </template>
  </section>
</template>

<style scoped>
.book { display: grid; gap: var(--space-3); min-width: 0; }
.head { display: flex; flex-wrap: wrap; align-items: flex-start; justify-content: space-between; gap: var(--space-2); }
.titles { display: grid; gap: 2px; min-width: 0; }
.h { margin: 0; font-size: 14px; font-weight: 600; }
.sub { margin: 0; font-size: 12px; color: var(--muted); }
.kinds { display: inline-flex; gap: 2px; padding: 2px; border: 1px solid var(--line); border-radius: var(--radius-sm); background: var(--panel2); }
.kinds button { height: 28px; padding: 0 10px; border: 0; border-radius: calc(var(--radius-sm) - 1px); background: none; color: var(--muted); font: inherit; font-size: 12px; font-weight: 600; cursor: pointer; }
.kinds button[aria-pressed='true'] { background: var(--panel); color: var(--text); box-shadow: var(--lift); }
.kinds button:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.state { margin: 0; padding: var(--space-3) 0; font-size: 13px; line-height: 1.5; color: var(--muted); max-width: 72ch; }
.state.warn { color: var(--warn); }

.ladder { display: grid; min-width: 0; font-size: 13px; font-variant-numeric: tabular-nums; }
.row { position: relative; display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr) minmax(0, 0.9fr); align-items: center; gap: var(--space-2); min-height: 34px; padding: 0 var(--space-3); }
.head-row { min-height: 26px; font-size: 11px; color: var(--muted); border-bottom: 1px solid var(--line); }
.row > * { position: relative; z-index: 1; }
.fill { position: absolute; top: 3px; bottom: 3px; right: 0; border-radius: 3px; z-index: 0; }
.ask .fill { background: color-mix(in srgb, var(--neg) 16%, transparent); }
.bid .fill { background: color-mix(in srgb, var(--pos) 16%, transparent); }
.ask .px { color: var(--neg); }
.bid .px { color: var(--pos); }
.px { font-weight: 600; line-height: 1.25; }
.px small { display: block; font-size: 10px; font-weight: 400; color: var(--muted); }
.num { text-align: right; }
.sz { font-weight: 600; }
.ex { font-size: 11px; color: var(--muted); }
.mid { border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); background: var(--panel2); font-size: 12px; color: var(--muted); }
.mid .px, .mid .sz { color: var(--text); }


.foot { margin: 0; font-size: 11px; line-height: 1.5; color: var(--muted); }
@media (max-width: 480px) {
  .row { grid-template-columns: minmax(0, 1fr) minmax(0, 0.8fr); }
  .row .ex { display: none; }
  .head-row span:last-child { display: none; }
}
</style>
