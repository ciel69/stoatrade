<script setup lang="ts">
import { computed } from 'vue'
import { UiChip } from '@/shared/ui'
import { duration, price, signed } from '@/shared/lib'
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
const units = (x: number) => x * 100
/** Смещение цены от входа в процентах в пользу позиции (минус: против неё). */
const pct = (p: number | null | undefined) => (p != null && props.position.avg ? (sign.value * (p - props.position.avg) / props.position.avg) * 100 : null)
const movePct = computed(() => pct(props.position.price))
/** К марже: из журнала сделок (после комиссии входа, как на старом дашборде); если журнала нет, грубо сдвиг цены × плечо. */
const roiPct = computed(() => props.position.pnl_pct ?? (movePct.value != null && props.position.lev ? movePct.value * props.position.lev : null))
const stopPct = computed(() => pct(props.position.stop))
const takePct = computed(() => pct(farTake.value))
</script>

<template>
  <li class="row">
    <div class="head">
      <span class="sym">{{ position.sym }}</span>
      <UiChip>{{ position.side === 'long' ? 'лонг' : 'шорт' }}{{ position.lev ? ' ' + position.lev + '×' : '' }}</UiChip>
      <UiChip v-if="position.stale" tone="warn">цена устарела</UiChip>
      <span class="pnl" :class="tone">{{ signed(position.upnl) }} <small>USDT</small></span>
      <span class="r" :class="tone" :title="'Результат к марже с учётом плеча и комиссии входа; цена от входа: ' + signed(movePct) + '%'">{{ signed(roiPct ?? movePct, 1) }}%</span>
    </div>
    <p class="sub">вход {{ price(position.avg) }} · сейчас {{ price(position.price) }} ({{ signed(movePct) }}%) · {{ signed(position.r, 2) }}R</p>

    <svg v-if="r" class="route" viewBox="0 0 100 14" preserveAspectRatio="none" role="img"
         :aria-label="`${position.sym}: от стопа ${price(position.stop)} через вход ${price(position.avg)} к тейку ${price(farTake)}, цена ${price(position.price)}`">
      <rect class="track" x="0" y="6" width="100" height="2" />
      <rect v-if="r.stop <= r.entry" class="risk" :x="0" y="4.5" :width="units(r.entry)" height="5" />
      <rect v-else class="locked" :x="units(r.entry)" y="4.5" :width="units(r.stop - r.entry)" height="5" />
      <rect v-if="r.price != null" class="gain" :class="{ loss: !r.inProfit }" :x="units(Math.min(r.entry, r.price))" y="4.5" :width="units(Math.abs(r.price - r.entry))" height="5" />
      <line v-for="(t, i) in r.targets" :key="i" class="take" :x1="units(t)" :x2="units(t)" y1="2" y2="12" />
      <line class="entry" :x1="units(r.entry)" :x2="units(r.entry)" y1="1" y2="13" />
      <line class="stop" :class="{ safe: r.stop > r.entry }" :x1="units(r.stop)" :x2="units(r.stop)" y1="0" y2="14" />
      <line v-if="r.price != null" class="now" x1="0" x2="0" y1="0" y2="14" :style="{ transform: `translateX(${units(r.price)}px)` }" />
    </svg>
    <p v-else class="muted">маршрут не построен: нет стопа или входа</p>

    <div class="marks">
      <span class="m stop-m">стоп {{ price(position.stop) }}<template v-if="stopPct != null"> ({{ signed(stopPct) }}%)</template><template v-if="atStop != null"> · <b :class="atStop > 0 ? 'up' : 'down'">{{ signed(atStop) }}</b></template><UiChip v-if="protectedStop" tone="ok">подтянут</UiChip></span>
      <span v-if="position.held_s != null" class="m held">{{ duration(position.held_s) }}</span>
      <span class="m take-m"><template v-if="farTake != null">тейк {{ price(farTake) }}</template><template v-if="takePct != null"> ({{ signed(takePct) }}%)</template><template v-if="atTake != null"> · <b class="up">{{ signed(atTake) }}</b></template></span>
    </div>
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
.route { display: block; width: 100%; height: 18px; margin: var(--space-2) 0 var(--space-1); overflow: visible; }
.track { fill: var(--line2); }
.risk { fill: var(--neg); opacity: 0.22; }
.locked { fill: var(--pos); opacity: 0.28; }
.stop.safe { stroke: var(--pos); }
.gain { fill: var(--pos); opacity: 0.55; }
.gain.loss { fill: var(--neg); opacity: 0.55; }
.take { stroke: var(--pos); stroke-width: 1.4; vector-effect: non-scaling-stroke; opacity: 0.8; }
.entry { stroke: var(--muted); stroke-width: 1.4; vector-effect: non-scaling-stroke; }
.stop { stroke: var(--neg); stroke-width: 2; vector-effect: non-scaling-stroke; }
.now { stroke: var(--text); stroke-width: 2.4; vector-effect: non-scaling-stroke; transition: transform 400ms var(--ease-out); }
.marks { display: flex; align-items: baseline; gap: var(--space-3); font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.m b { font-weight: 600; }
.stop-m { flex: 1 1 0; }
.take-m { flex: 1 1 0; text-align: right; }
.held { margin-left: var(--space-2); }
.muted { margin: var(--space-2) 0 0; color: var(--muted); font-size: 12px; }
@media (prefers-reduced-motion: reduce) {
  .now { transition: none; }
}
</style>
