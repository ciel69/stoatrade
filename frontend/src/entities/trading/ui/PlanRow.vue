<script setup lang="ts">
import { computed } from 'vue'
import { UiChip } from '@/shared/ui'
import { countdown, fixed, price } from '@/shared/lib'
import { planRoute } from '../lib/route'
import { planWait } from '../lib/plan-wait'
import type { NearPlan } from '../model/types'

/**
 * План до входа в той же графике, что открытая позиция: полоса от стопа к цели, вход (уровень условия) акцентом, цена сейчас, а между ценой и входом отрезок «осталось дойти».
 * Над полосой сказано словами, чего план ждёт и сколько до уровня; под ней стоп, риск и цель в процентах от входа и когда ближайшая проверка.
 */
const props = defineProps<{ plan: NearPlan; now: number }>()
const NEAR = 0.3        // % до триггера, с которого план считается «у входа»

const w = computed(() => planWait(props.plan, props.now))
const r = computed(() => planRoute(props.plan))
const units = (x: number) => x * 100
const entry = computed(() => props.plan.level ?? props.plan.price)
/** Цена выше уровня (ждём снижения) или ниже (ждём роста). */
const arrow = computed(() => (props.plan.price != null && props.plan.level != null ? (props.plan.price > props.plan.level ? '↓' : props.plan.price < props.plan.level ? '↑' : '') : ''))
const candleWait = computed(() => props.plan.beyond === true && props.plan.kind != null && /^(candle|volume)/.test(props.plan.kind))
const near = computed(() => props.plan.dist_pct != null && props.plan.dist_pct <= NEAR)
const eta = computed(() => (props.plan.eta_at != null && props.plan.eta_at > props.now ? props.plan.eta_at - props.now : null))
const farTake = computed(() => {
  const t = props.plan.targets?.length ? props.plan.targets : props.plan.target != null ? [props.plan.target] : []
  if (!t.length || props.plan.side == null) return null
  return props.plan.side === 'long' ? Math.max(...t) : Math.min(...t)
})
/** Расстояние от входа в процентах (стоп: риск, цель: потенциал). */
const fromEntry = (p: number | null | undefined) => (p != null && entry.value ? (Math.abs(p - entry.value) / entry.value) * 100 : null)
const stopPct = computed(() => fromEntry(props.plan.stop))
const takePct = computed(() => fromEntry(farTake.value))
const chase = computed(() => (props.plan.blocked_why === 'CHASE' && props.plan.chase_bps != null ? `цена на ${fixed(props.plan.chase_bps / 100, 2)}% дальше уровня, допуск ${fixed((props.plan.chase_max_bps ?? 0) / 100, 2)}%` : null))
</script>

<template>
  <li class="row">
    <div class="head">
      <span class="sym">{{ plan.sym }}</span>
      <UiChip>{{ plan.side === 'long' ? 'лонг' : plan.side === 'short' ? 'шорт' : '?' }}{{ plan.leverage ? ' ' + plan.leverage + '×' : '' }}</UiChip>
      <UiChip v-if="plan.setup">{{ plan.setup }}</UiChip>
      <UiChip v-if="plan.mode === 'paper'">paper</UiChip>
      <UiChip v-if="plan.status === 'draft'" tone="warn">черновик</UiChip>
      <UiChip v-if="plan.entry_state === 'resting'" tone="ok">лимит стоит</UiChip>
      <span class="dist" :class="{ near }" :title="candleWait ? 'цена уже за уровнем, вход по закрытию свечи' : 'до уровня условия входа'">
        <template v-if="candleWait">ждёт свечу</template>
        <template v-else>{{ near ? '' : arrow + ' ' }}{{ plan.dist_pct != null ? fixed(plan.dist_pct) + '%' : '–' }}</template>
      </span>
      <span v-if="eta != null" class="eta" :title="'до закрытия свечи ' + (plan.tf ?? '')">{{ countdown(eta) }}</span>
    </div>
    <p class="sub">{{ plan.level != null ? 'вход у ' + price(plan.level) : 'вход по условию' }} · сейчас {{ price(plan.price) }}<template v-if="plan.price != null && plan.level != null && !candleWait"> · {{ near ? 'цена у уровня' : plan.price > plan.level ? 'ждём снижения' : 'ждём роста' }}</template></p>

    <p class="wait" :class="`t-${w.tone}`">{{ w.headline }}<UiChip v-if="w.progress" class="prog">{{ w.progress }}</UiChip></p>
    <p v-if="chase" class="wait t-blocked small">{{ chase }}</p>
    <ul v-if="w.rest.length" class="rest">
      <li v-for="(x, i) in w.rest" :key="i">{{ x }}</li>
    </ul>

    <svg v-if="r" class="route" viewBox="0 0 100 14" preserveAspectRatio="none" role="img"
         :aria-label="`${plan.sym}: от стопа ${price(plan.stop)} через вход ${price(entry)} к цели ${price(farTake)}, цена ${price(plan.price)}`">
      <rect class="track" x="0" y="6" width="100" height="2" />
      <rect class="risk" x="0" y="4.5" :width="units(r.entry)" height="5" />
      <rect v-if="r.gap" class="gap" :x="units(r.gap[0])" y="4.5" :width="units(r.gap[1] - r.gap[0])" height="5" />
      <line v-for="(t, i) in r.targets" :key="i" class="take" :x1="units(t)" :x2="units(t)" y1="2" y2="12" />
      <line class="entry" :x1="units(r.entry)" :x2="units(r.entry)" y1="0" y2="14" />
      <line class="stop" x1="0" x2="0" y1="0" y2="14" />
      <line v-if="r.price != null" class="now" x1="0" x2="0" y1="0" y2="14" :style="{ transform: `translateX(${units(r.price)}px)` }" />
    </svg>

    <div class="marks">
      <span class="m stop-m">стоп {{ price(plan.stop) }}<template v-if="stopPct != null"> (−{{ fixed(stopPct) }}%)</template><template v-if="plan.risk != null"> · <b class="down">−{{ fixed(plan.risk, 2) }}</b></template></span>
      <span v-if="plan.net_rr != null" class="m mid">RR {{ fixed(plan.net_rr, 1) }}</span>
      <span class="m take-m"><template v-if="farTake != null">цель {{ price(farTake) }}</template><template v-if="takePct != null"> (+{{ fixed(takePct) }}%)</template></span>
    </div>
    <p class="when">{{ w.when }}</p>
  </li>
</template>

<style scoped>
.row { padding: var(--space-3) 0; border-top: 1px solid var(--line); }
.row:first-child { border-top: 0; padding-top: 0; }
.head { display: flex; align-items: center; flex-wrap: wrap; gap: var(--space-2); }
.sym { font-size: 17px; font-weight: 700; letter-spacing: -0.01em; }
.dist { margin-left: auto; font-size: 17px; font-weight: 700; font-variant-numeric: tabular-nums; color: var(--muted); }
.dist.near { color: var(--pos); }
.eta { min-width: 48px; text-align: right; font-size: 17px; font-weight: 700; font-variant-numeric: tabular-nums; color: var(--muted); }
.sub { margin: 2px 0 0; font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.wait { margin: var(--space-2) 0 0; font-size: 13px; line-height: 1.4; overflow-wrap: anywhere; display: flex; align-items: baseline; flex-wrap: wrap; gap: 0 var(--space-2); }
.wait.small { font-size: 12px; margin-top: 2px; }
.t-blocked { color: var(--warn); }
.t-paused { color: var(--muted); }
.prog { flex: none; }
.rest { margin: 2px 0 0; padding: 0 0 0 16px; font-size: 12px; color: var(--muted); }
.rest li { overflow-wrap: anywhere; }
.route { display: block; width: 100%; height: 18px; margin: var(--space-2) 0 var(--space-1); overflow: visible; }
.track { fill: var(--line2); }
.risk { fill: var(--neg); opacity: 0.22; }
.gap { fill: var(--accent); opacity: 0.5; }
.take { stroke: var(--pos); stroke-width: 1.4; vector-effect: non-scaling-stroke; opacity: 0.8; }
.entry { stroke: var(--accent); stroke-width: 2; vector-effect: non-scaling-stroke; }
.stop { stroke: var(--neg); stroke-width: 2; vector-effect: non-scaling-stroke; }
.now { stroke: var(--text); stroke-width: 2.4; vector-effect: non-scaling-stroke; transition: transform 400ms var(--ease-out); }
.marks { display: flex; align-items: baseline; gap: var(--space-3); font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.m b { font-weight: 600; }
.down { color: var(--neg); }
.stop-m { flex: 1 1 0; }
.mid { flex: none; }
.take-m { flex: 1 1 0; text-align: right; }
.when { margin: var(--space-1) 0 0; font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
@media (prefers-reduced-motion: reduce) {
  .now { transition: none; }
}
</style>
