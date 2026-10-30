<script setup lang="ts">
import { computed } from 'vue'
import { UiChip } from '@/shared/ui'
import { countdown, duration, fixed, price } from '@/shared/lib'
import { planRoute } from '../lib/route'
import { planWait } from '../lib/plan-wait'
import type { NearPlan } from '../model/types'

/**
 * План до входа почти без слов. Полоса как у открытой позиции: стоп слева, вход (уровень условия) акцентом с ценой над ним, цели справа, цена белой меткой, а между ценой и входом
 * синий отрезок «осталось дойти» (его длина и есть расстояние). Справа в заголовке процент до уровня и кольцо до закрытия свечи; точки показывают, сколько условий выполнено.
 * Словами остаётся одна строка: невыполненное условие (или причина паузы, блокировки). Остальное (сетап, тезис, риск в USDT, RR, срок плана) в подсказке.
 */
const props = defineProps<{ plan: NearPlan; now: number }>()
const NEAR = 0.3        // % до триггера, с которого план считается «у входа»
const TF_S: Record<string, number> = { м: 60, m: 60, ч: 3600, h: 3600, д: 86400, d: 86400 }

const w = computed(() => planWait(props.plan, props.now))
const r = computed(() => planRoute(props.plan))
const units = (x: number) => x * 100
const entry = computed(() => props.plan.level ?? props.plan.price)
const near = computed(() => props.plan.dist_pct != null && props.plan.dist_pct <= NEAR)
const eta = computed(() => (props.plan.eta_at != null && props.plan.eta_at > props.now ? props.plan.eta_at - props.now : null))
/** Доля пройденного времени свечи 0..1 для кольца. */
const tfSec = computed(() => {
  const m = /^(\d+)\s*([мчдmhd])/i.exec(props.plan.tf ?? '')
  return m ? Number(m[1]) * (TF_S[m[2].toLowerCase()] ?? 0) : 0
})
const ring = computed(() => (eta.value != null && tfSec.value > 0 ? Math.min(1, Math.max(0, 1 - eta.value / tfSec.value)) : null))
const RING = 2 * Math.PI * 6
const farTake = computed(() => {
  const t = props.plan.targets?.length ? props.plan.targets : props.plan.target != null ? [props.plan.target] : []
  if (!t.length || props.plan.side == null) return null
  return props.plan.side === 'long' ? Math.max(...t) : Math.min(...t)
})
const fromEntry = (p: number | null | undefined) => (p != null && entry.value ? (Math.abs(p - entry.value) / entry.value) * 100 : null)
const stopPct = computed(() => fromEntry(props.plan.stop))
const takePct = computed(() => fromEntry(farTake.value))
/** Точки условий: сколько из них выполнено. */
const dots = computed(() => {
  const total = props.plan.seq?.total ?? props.plan.leaf_n
  const done = props.plan.seq ? props.plan.seq.done : props.plan.met_n
  return total > 1 ? Array.from({ length: total }, (_, i) => i < done) : []
})
/** Единственная строка словами. */
const line = computed(() => (w.value.tone === 'wait' ? (props.plan.missing[0] ?? props.plan.text) : w.value.headline))
/** Метка уровня над полосой: центр по входу, не дальше краёв. */
const entryLeft = computed(() => (r.value ? Math.min(90, Math.max(10, units(r.value.entry))) : 50))
/** Срок плана, если он скоро кончится и не совпадает с отсчётом до свечи (иначе на карточке два одинаковых таймера). */
const ttlSoon = computed(() => {
  const left = props.plan.ttl_at != null ? props.plan.ttl_at - props.now : null
  if (left == null || left <= 0 || left > 900) return null
  return eta.value != null && Math.abs(left - eta.value) <= 20 ? null : left
})
const tip = computed(() => {
  const p = props.plan
  const bits = [p.setup, p.entry_text && `вход: ${p.entry_text}`, p.missing.length > 1 ? `ещё: ${p.missing.slice(1).join('; ')}` : '', p.price != null ? `цена ${price(p.price)}` : '',
    p.risk != null ? `риск ${fixed(p.risk, 2)} USDT` : '', p.net_rr != null ? `RR ${fixed(p.net_rr, 1)}` : '', p.ttl_at != null && p.ttl_at > props.now ? `план истекает через ${duration(p.ttl_at - props.now)}` : '', p.thesis]
  return bits.filter(Boolean).join(' · ')
})
</script>

<template>
  <li class="row" :title="tip">
    <div class="head">
      <span class="sym">{{ plan.sym }}</span>
      <UiChip>{{ plan.side === 'long' ? 'лонг' : plan.side === 'short' ? 'шорт' : '?' }}{{ plan.leverage ? ' ' + plan.leverage + '×' : '' }}</UiChip>
      <UiChip v-if="plan.status === 'draft'" tone="warn">черновик</UiChip>
      <UiChip v-if="plan.entry_state === 'resting'" tone="ok">лимит стоит</UiChip>
      <span class="dist" :class="{ near }">{{ plan.dist_pct != null ? fixed(plan.dist_pct) + '%' : '–' }}</span>
      <span v-if="eta != null" class="timer" :title="'до закрытия свечи ' + (plan.tf ?? '')">
        <svg v-if="ring != null" class="ring" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
          <circle class="ring-bg" cx="8" cy="8" r="6" />
          <circle class="ring-fg" cx="8" cy="8" r="6" :style="{ strokeDasharray: RING, strokeDashoffset: RING * (1 - ring) }" />
        </svg>
        {{ countdown(eta) }}
      </span>
    </div>

    <p class="line" :class="`t-${w.tone}`">
      <span v-if="dots.length" class="dots" :title="plan.missing.join('; ')" aria-hidden="true"><i v-for="(d, i) in dots" :key="i" :class="{ on: d }" /></span>
      <span class="txt">{{ line }}</span>
      <span v-if="ttlSoon != null" class="ttl">план {{ countdown(ttlSoon) }}</span>
    </p>

    <div v-if="r" class="viz">
      <span class="lvl" :style="{ left: entryLeft + '%' }">{{ price(entry) }}</span>
      <svg class="route" viewBox="0 0 100 14" preserveAspectRatio="none" role="img"
           :aria-label="`${plan.sym}: от стопа ${price(plan.stop)} через вход ${price(entry)} к цели ${price(farTake)}, цена ${price(plan.price)}`">
        <rect class="track" x="0" y="6" width="100" height="2" />
        <rect class="risk" x="0" y="4.5" :width="units(r.entry)" height="5" />
        <rect v-if="r.gap" class="gap" :x="units(r.gap[0])" y="4.5" :width="units(r.gap[1] - r.gap[0])" height="5" />
        <line v-for="(t, i) in r.targets" :key="i" class="take" :x1="units(t)" :x2="units(t)" y1="2" y2="12" />
        <line class="entry" :x1="units(r.entry)" :x2="units(r.entry)" y1="0" y2="14" />
        <line class="stop" x1="0" x2="0" y1="0" y2="14" />
        <line v-if="r.price != null" class="now" x1="0" x2="0" y1="0" y2="14" :style="{ transform: `translateX(${units(r.price)}px)` }" />
      </svg>
      <div class="ends">
        <span class="down">{{ price(plan.stop) }}<template v-if="stopPct != null"> −{{ fixed(stopPct) }}%</template></span>
        <span v-if="farTake != null" class="up">{{ price(farTake) }}<template v-if="takePct != null"> +{{ fixed(takePct) }}%</template></span>
      </div>
    </div>
  </li>
</template>

<style scoped>
.row { padding: var(--space-3) 0; border-top: 1px solid var(--line); }
.row:first-child { border-top: 0; padding-top: 0; }
.head { display: flex; align-items: center; gap: var(--space-2); }
.sym { font-size: 17px; font-weight: 700; letter-spacing: -0.01em; }
.dist { margin-left: auto; font-size: 17px; font-weight: 700; font-variant-numeric: tabular-nums; color: var(--muted); }
.dist.near { color: var(--pos); }
.timer { display: inline-flex; align-items: center; gap: 5px; min-width: 56px; justify-content: flex-end; font-size: 13px; font-weight: 600; font-variant-numeric: tabular-nums; color: var(--muted); }
.ring { flex: none; transform: rotate(-90deg); }
.ring circle { fill: none; stroke-width: 2.2; }
.ring-bg { stroke: var(--line2); }
.ring-fg { stroke: var(--accent); stroke-linecap: round; transition: stroke-dashoffset 1000ms linear; }
.line { margin: 2px 0 0; display: flex; align-items: center; gap: var(--space-2); min-width: 0; font-size: 12px; color: var(--muted); }
.txt { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.t-blocked .txt { color: var(--warn); }
.dots { display: inline-flex; gap: 3px; flex: none; }
.dots i { width: 6px; height: 6px; border-radius: 50%; background: var(--line2); }
.dots i.on { background: var(--accent); }
.ttl { margin-left: auto; flex: none; color: var(--warn); font-variant-numeric: tabular-nums; }
.viz { position: relative; padding-top: 14px; margin-top: var(--space-1); }
.lvl { position: absolute; top: 0; transform: translateX(-50%); font-size: 11px; font-weight: 600; color: var(--accent); font-variant-numeric: tabular-nums; white-space: nowrap; }
.route { display: block; width: 100%; height: 18px; overflow: visible; }
.track { fill: var(--line2); }
.risk { fill: var(--neg); opacity: 0.22; }
.gap { fill: var(--accent); opacity: 0.5; }
.take { stroke: var(--pos); stroke-width: 1.4; vector-effect: non-scaling-stroke; opacity: 0.8; }
.entry { stroke: var(--accent); stroke-width: 2; vector-effect: non-scaling-stroke; }
.stop { stroke: var(--neg); stroke-width: 2; vector-effect: non-scaling-stroke; }
.now { stroke: var(--text); stroke-width: 2.4; vector-effect: non-scaling-stroke; transition: transform 400ms var(--ease-out); }
.ends { display: flex; justify-content: space-between; margin-top: 3px; font-size: 11px; font-variant-numeric: tabular-nums; }
.up { color: var(--pos); }
.down { color: var(--neg); }
@media (prefers-reduced-motion: reduce) {
  .now, .ring-fg { transition: none; }
}
</style>
