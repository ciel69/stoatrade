<script setup lang="ts">
import { computed } from 'vue'
import { UiChip, UiRouteStrip, UiTip, type StripBand, type StripMark } from '@/shared/ui'
import { countdown, fixed, price } from '@/shared/lib'
import { RESTING_LABEL, captionHints, distHint, dotsHint, draftHint, planInfoHint, restingHint, sideHint, timerHint } from '../lib/hints'
import { planRoute } from '../lib/route'
import { planWait } from '../lib/plan-wait'
import type { NearPlan } from '../model/types'

/**
 * План до входа почти без слов. Полоса как у открытой позиции: стоп слева, вход (уровень условия) акцентом с ценой над ним, цели справа, цена белой меткой, а между ценой и входом
 * синий отрезок «осталось дойти» (его длина и есть расстояние). Справа в заголовке процент до уровня и кольцо до закрытия свечи; точки показывают, сколько условий выполнено.
 * Словами остаётся одна строка: невыполненное условие (или причина паузы, блокировки). Остальное (идея, сетап, риск в USDT, RR, срок плана) в подсказке у названия монеты.
 * Подсказки (UiTip, тексты в lib/hints.ts) есть у чипа стороны, заявки на бирже, процента, таймера, точек условий и меток полосы; нативного title нет.
 */
const props = defineProps<{ plan: NearPlan; now: number }>()
const NEAR = 0.3        // % до триггера, с которого план считается «у входа»
const TF_S: Record<string, number> = { м: 60, m: 60, ч: 3600, h: 3600, д: 86400, d: 86400 }

const w = computed(() => planWait(props.plan, props.now))
const r = computed(() => planRoute(props.plan))
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
const dotsDone = computed(() => (props.plan.seq ? props.plan.seq.done : props.plan.met_n))
const dots = computed(() => {
  const total = props.plan.seq?.total ?? props.plan.leaf_n
  const done = props.plan.seq ? props.plan.seq.done : props.plan.met_n
  return total > 1 ? Array.from({ length: total }, (_, i) => i < done) : []
})
/** Свечное условие: цена уже за уровнем, но вход только по закрытию свечи (касание входом не считается). */
const candle = computed(() => props.plan.kind === 'candle_close' || props.plan.kind === 'candle_reject')
const crossed = computed(() => candle.value && props.plan.beyond === true)
/** Прошлая свеча прокалывала уровень и закрылась обратно: почему входа не было. */
const pierce = computed(() => {
  const b = props.plan.last_bar
  if (!candle.value || !b?.pierced) return null
  const ext = props.plan.dir === 'below' ? b.l : b.h
  return `прокол до ${price(ext)}, свеча закрылась ${props.plan.dir === 'below' ? 'выше' : 'ниже'} (${price(b.c)})`
})
/** Единственная строка словами. */
const line = computed(() => {
  if (w.value.tone !== 'wait') return w.value.headline
  if (crossed.value) return `цена за уровнем · ждём закрытия свечи${eta.value != null ? ' через ' + countdown(eta.value) : ''}`
  return pierce.value ?? props.plan.missing[0] ?? props.plan.text
})
const dir = computed(() => (props.plan.side === 'short' ? -1 : 1))
/** Смещение цены от уровня входа в пользу плана, %. */
const fromEntrySigned = (p: number | null | undefined) => (p != null && entry.value ? (dir.value * (p - entry.value) / entry.value) * 100 : null)
/** Цели в том же порядке и с тем же отбором, что в planRoute(). */
const goals = computed(() => (props.plan.targets?.length ? props.plan.targets : props.plan.target != null ? [props.plan.target] : []).filter((x) => entry.value != null && dir.value * (x - entry.value) > 0))
const marks = computed<StripMark[]>(() => {
  const x = r.value
  if (!x) return []
  const p = props.plan
  const out: StripMark[] = [
    { key: 'stop', kind: 'stop', at: x.stop, name: 'Стоп', price: p.stop, pct: fromEntrySigned(p.stop), money: p.risk != null ? -p.risk : null },
    { key: 'entry', kind: 'level', at: x.entry, name: 'Вход', price: entry.value },
  ]
  x.targets.forEach((at, i) => {
    const last = farTake.value != null && i === goals.value.lastIndexOf(farTake.value)
    out.push({ key: last ? 'take' : `take-${i}`, kind: 'take', at, name: goals.value.length > 1 ? `Тейк ${i + 1}` : 'Тейк', price: goals.value[i] ?? null, pct: fromEntrySigned(goals.value[i]) })
  })
  if (x.price != null) out.push({ key: 'now', kind: 'now', at: x.price, name: 'Цена', price: p.price, pct: fromEntrySigned(p.price) })
  return out
})
const bands = computed<StripBand[]>(() => {
  const x = r.value
  if (!x) return []
  const out: StripBand[] = [{ from: 0, to: x.entry, kind: 'risk' }]
  if (x.gap) out.push({ from: x.gap[0], to: x.gap[1], kind: 'gap' })
  return out
})
/** Срок плана, если он скоро кончится и не совпадает с отсчётом до свечи (иначе на карточке два одинаковых таймера). */
const ttlSoon = computed(() => {
  const left = props.plan.ttl_at != null ? props.plan.ttl_at - props.now : null
  if (left == null || left <= 0 || left > 900) return null
  return eta.value != null && Math.abs(left - eta.value) <= 20 ? null : left
})
</script>

<template>
  <li class="row">
    <div class="head">
      <UiTip class="sym-tip" dotted :tip="planInfoHint(plan, now)"><span class="sym">{{ plan.sym }}</span></UiTip>
      <UiTip :tip="sideHint(plan.side, plan.leverage)"><UiChip>{{ plan.side === 'long' ? 'лонг' : plan.side === 'short' ? 'шорт' : '?' }}{{ plan.leverage ? ' ' + plan.leverage + '×' : '' }}</UiChip></UiTip>
      <UiTip v-if="plan.status === 'draft'" :tip="draftHint"><UiChip tone="warn">черновик</UiChip></UiTip>
      <UiTip v-if="plan.entry_state === 'resting'" :tip="restingHint"><UiChip tone="ok">{{ RESTING_LABEL }}</UiChip></UiTip>
      <UiTip class="push" :tip="distHint(crossed)"><span class="dist" :class="{ near, crossed }">{{ plan.dist_pct != null ? (crossed ? 'за ' : '') + fixed(plan.dist_pct) + '%' : '–' }}</span></UiTip>
      <UiTip v-if="eta != null" :tip="timerHint(plan.tf)">
        <span class="timer">
          <svg v-if="ring != null" class="ring" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
            <circle class="ring-bg" cx="8" cy="8" r="6" />
            <circle class="ring-fg" cx="8" cy="8" r="6" :style="{ strokeDasharray: RING, strokeDashoffset: RING * (1 - ring) }" />
          </svg>
          {{ countdown(eta) }}
        </span>
      </UiTip>
    </div>

    <p class="line" :class="[`t-${w.tone}`, { 't-crossed': crossed }]">
      <UiTip v-if="dots.length" :tip="dotsHint(dotsDone, dots.length, plan.missing)" :label="`Условия входа: выполнено ${dotsDone} из ${dots.length}`"><span class="dots" aria-hidden="true"><i v-for="(d, i) in dots" :key="i" :class="{ on: d }" /></span></UiTip>
      <span class="txt">{{ line }}</span>
      <span v-if="ttlSoon != null" class="ttl">план {{ countdown(ttlSoon) }}</span>
    </p>

    <UiRouteStrip v-if="r" class="strip" :marks="marks" :bands="bands" :flag="{ at: r.entry, text: price(entry), markKey: 'entry' }"
                  :label="`${plan.sym}: от стопа ${price(plan.stop)} через вход ${price(entry)} к цели ${price(farTake)}, цена ${price(plan.price)}`">
      <template #caption="{ active, set }">
        <span class="rs-i" :class="{ hot: active === 'stop' }" @pointerenter="set('stop')" @pointerleave="set(null)">
          <UiTip :tip="captionHints.stop"><span class="down">{{ price(plan.stop) }}<template v-if="stopPct != null"> −{{ fixed(stopPct) }}%</template></span></UiTip>
        </span>
        <span v-if="farTake != null" class="rs-i rs-end" :class="{ hot: active === 'take' }" @pointerenter="set('take')" @pointerleave="set(null)">
          <UiTip :tip="captionHints.take"><span class="up">{{ price(farTake) }}<template v-if="takePct != null"> +{{ fixed(takePct) }}%</template></span></UiTip>
        </span>
      </template>
    </UiRouteStrip>
  </li>
</template>

<style scoped>
.row { padding: var(--space-3) 0; border-top: 1px solid var(--line); }
.row:first-child { border-top: 0; padding-top: 0; }
.head { display: flex; align-items: center; gap: var(--space-2); }
.sym { font-size: 17px; font-weight: 700; letter-spacing: -0.01em; }
.head :deep(.push) { margin-left: auto; }        /* у UiTip два корня, поэтому scoped-метка родителя на кнопку не ставится */
.dist { font-size: 17px; font-weight: 700; font-variant-numeric: tabular-nums; color: var(--muted); }
.dist.near { color: var(--accent); }
.dist.crossed { color: var(--warn); }
.t-crossed .txt { color: var(--warn); }
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
.strip { margin-top: var(--space-2); }
.up { color: var(--pos); }
.down { color: var(--neg); }
@media (prefers-reduced-motion: reduce) {
  .ring-fg { transition: none; }
}
</style>
