<script setup lang="ts">
import { computed } from 'vue'
import { UiChip, UiRouteStrip, UiTip, type StripBand, type StripMark } from '@/shared/ui'
import { duration, price, signed } from '@/shared/lib'
import { captionHints, roiHint, sideHint, staleHint, stopMoveKind, stopMovedHint } from '../lib/hints'
import { route } from '../lib/route'
import type { Position } from '../model/types'

const props = defineProps<{ position: Position }>()

const sign = computed(() => (props.position.side === 'long' ? 1 : -1))
const r = computed(() => route(props.position))
const farTake = computed(() => {
  const t = props.position.targets.map((x) => x.price).filter((x) => props.position.avg != null && sign.value * (x - props.position.avg) > 0)
  return t.length ? (sign.value > 0 ? Math.max(...t) : Math.min(...t)) : null
})
/** Результат при срабатывании стопа и при последнем тейке, в USDT (нужен размер позиции). */
const atStop = computed(() => props.position.pnl_at_sl ?? (props.position.risk != null ? -props.position.risk : null))
const atTake = computed(() => (props.position.qty != null && props.position.avg != null && farTake.value != null ? props.position.qty * sign.value * (farTake.value - props.position.avg) : null))
const tone = computed(() => ((props.position.upnl ?? 0) > 0 ? 'up' : (props.position.upnl ?? 0) < 0 ? 'down' : ''))
/** Стоп сдвинут от начального (автозащита или правило): это важнее цифры стопа */
const protectedStop = computed(() => props.position.stop != null && props.position.stop0 != null && props.position.stop !== props.position.stop0)
/** Куда подтянут: за вход (прибыль зафиксирована) или ещё на стороне убытка (убыток урезан). */
const stopMove = computed(() => (protectedStop.value && props.position.avg != null ? stopMoveKind(props.position.side, props.position.stop as number, props.position.avg) : null))
/** Смещение цены от входа в процентах в пользу позиции (минус: против неё). */
const pct = (p: number | null | undefined) => (p != null && props.position.avg ? (sign.value * (p - props.position.avg) / props.position.avg) * 100 : null)
const movePct = computed(() => pct(props.position.price))
/** К марже: из журнала сделок (после комиссии входа, как на старом дашборде); если журнала нет, грубо сдвиг цены × плечо. */
const roiPct = computed(() => props.position.pnl_pct ?? (movePct.value != null && props.position.lev ? movePct.value * props.position.lev : null))
const stopPct = computed(() => pct(props.position.stop))
const takePct = computed(() => pct(farTake.value))

/** Деления и закрашенные отрезки полосы; цели берутся в том же порядке и с тем же отбором, что в route(). */
const goals = computed(() => props.position.targets.map((x) => x.price).filter((x) => props.position.avg != null && sign.value * (x - props.position.avg) > 0))
const marks = computed<StripMark[]>(() => {
  const x = r.value
  if (!x) return []
  const p = props.position
  const out: StripMark[] = [
    { key: 'stop', kind: x.stop > x.entry ? 'safe' : 'stop', at: x.stop, name: protectedStop.value ? 'Стоп подтянут' : 'Стоп', price: p.stop, pct: stopPct.value, money: atStop.value },
    { key: 'entry', kind: 'entry', at: x.entry, name: 'Вход', price: p.avg },
  ]
  x.targets.forEach((at, i) => {
    const last = farTake.value != null && i === goals.value.lastIndexOf(farTake.value)
    out.push({ key: last ? 'take' : `take-${i}`, kind: 'take', at, name: goals.value.length > 1 ? `Тейк ${i + 1}` : 'Тейк', price: goals.value[i] ?? null, pct: pct(goals.value[i]), money: last ? atTake.value : null })
  })
  if (x.price != null) out.push({ key: 'now', kind: 'now', at: x.price, name: 'Сейчас', price: p.price, pct: movePct.value, money: p.upnl })
  return out
})
const bands = computed<StripBand[]>(() => {
  const x = r.value
  if (!x) return []
  const out: StripBand[] = [x.stop <= x.entry ? { from: 0, to: x.entry, kind: 'risk' } : { from: x.entry, to: x.stop, kind: 'locked' }]
  if (x.price != null) out.push({ from: Math.min(x.entry, x.price), to: Math.max(x.entry, x.price), kind: x.inProfit ? 'gain' : 'loss' })
  return out
})
</script>

<template>
  <li class="row">
    <div class="head">
      <span class="sym">{{ position.sym }}</span>
      <UiTip :tip="sideHint(position.side, position.lev)"><UiChip>{{ position.side === 'long' ? 'лонг' : 'шорт' }}{{ position.lev ? ' ' + position.lev + '×' : '' }}</UiChip></UiTip>
      <UiTip v-if="position.stale" :tip="staleHint"><UiChip tone="warn">цена устарела</UiChip></UiTip>
      <span class="pnl" :class="tone">{{ signed(position.upnl) }} <small>USDT</small></span>
      <UiTip :tip="roiHint(signed(movePct))"><span class="r" :class="tone">{{ signed(roiPct ?? movePct, 1) }}%</span></UiTip>
    </div>
    <p class="sub">вход {{ price(position.avg) }} · сейчас {{ price(position.price) }} ({{ signed(movePct) }}%) · {{ signed(position.r, 2) }}R</p>

    <UiRouteStrip v-if="r" class="strip" :marks="marks" :bands="bands" :label="`${position.sym}: от стопа ${price(position.stop)} через вход ${price(position.avg)} к тейку ${price(farTake)}, цена ${price(position.price)}`">
      <template #caption="{ active, set }">
        <span class="rs-i" :class="{ hot: active === 'stop' }" @pointerenter="set('stop')" @pointerleave="set(null)">
          <span><UiTip :tip="captionHints.stop">стоп</UiTip> {{ price(position.stop) }}<template v-if="stopPct != null"> ({{ signed(stopPct) }}%)</template><template v-if="atStop != null"> · <b :class="atStop > 0 ? 'up' : 'down'">{{ signed(atStop) }}</b></template></span>
          <UiTip v-if="stopMove" :tip="stopMovedHint(stopMove)"><UiChip tone="ok">подтянут</UiChip></UiTip>
        </span>
        <span v-if="position.held_s != null" class="rs-i rs-mid">{{ duration(position.held_s) }}</span>
        <span v-if="farTake != null" class="rs-i rs-end" :class="{ hot: active === 'take' }" @pointerenter="set('take')" @pointerleave="set(null)">
          <span><UiTip :tip="captionHints.take">тейк</UiTip> {{ price(farTake) }}<template v-if="takePct != null"> ({{ signed(takePct) }}%)</template><template v-if="atTake != null"> · <b class="up">{{ signed(atTake) }}</b></template></span>
        </span>
      </template>
    </UiRouteStrip>
    <p v-else class="muted">маршрут не построен: нет стопа или входа</p>
  </li>
</template>

<style scoped>
.row { padding: var(--space-3) 0; border-top: 1px solid var(--line); }
.row:first-child { border-top: 0; padding-top: 0; }
.head { display: flex; align-items: center; gap: var(--space-2); }
.sym { font-size: 17px; font-weight: 700; letter-spacing: -0.01em; }
.pnl { margin-left: auto; font-size: 17px; font-weight: 700; font-variant-numeric: tabular-nums; }
.pnl small { font-size: 11px; font-weight: 400; color: var(--muted); }
.r { min-width: 64px; text-align: right; font-size: 17px; font-weight: 700; font-variant-numeric: tabular-nums; color: var(--muted); }
.sub { margin: 2px 0 0; font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.pnl.up, .r.up, .up { color: var(--pos); }
.pnl.down, .r.down, .down { color: var(--neg); }
.strip { margin-top: var(--space-3); }
.muted { margin: var(--space-2) 0 0; color: var(--muted); font-size: 12px; }
</style>
