<script setup lang="ts">
import { computed } from 'vue'
import { UiChip } from '@/shared/ui'
import { price, signed } from '@/shared/lib'
import type { NearPlan } from '../model/types'

/** Закрытый или ещё не вошедший план одной строкой: монета, сторона, чем кончился, результат и время; полосы нет, плана больше нет в ожидании. */
const props = defineProps<{ plan: NearPlan }>()
const STATUS_RU: Record<string, string> = { closed: 'закрыт', cancelled: 'отменён', expired: 'истёк', invalidated: 'отозван', rejected: 'отклонён', entered: 'вошёл', managing: 'сопровождается' }
const REASON_RU: Record<string, string> = { tp: 'тейк', sl: 'стоп', stop: 'стоп', target: 'цель', manual: 'вручную', ttl: 'срок', timeout: 'срок' }
const dateFmt = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false })
const status = computed(() => STATUS_RU[props.plan.status ?? ''] ?? props.plan.status ?? '–')
const reason = computed(() => (props.plan.close_reason ? REASON_RU[props.plan.close_reason] ?? props.plan.close_reason : null))
const when = computed(() => (props.plan.ts_closed != null ? dateFmt.format(new Date(props.plan.ts_closed * 1000)) : null))
const tone = computed(() => ((props.plan.close_pnl ?? 0) > 0 ? 'up' : (props.plan.close_pnl ?? 0) < 0 ? 'down' : ''))
</script>

<template>
  <li class="row">
    <div class="head">
      <span class="sym">{{ plan.sym }}</span>
      <UiChip>{{ plan.side === 'long' ? 'лонг' : plan.side === 'short' ? 'шорт' : '?' }}</UiChip>
      <UiChip>{{ status }}</UiChip>
      <span v-if="reason" class="reason">{{ reason }}</span>
      <span v-if="plan.close_pnl != null && plan.status === 'closed'" class="pnl" :class="tone">{{ signed(plan.close_pnl, 3) }} <small>USDT</small></span>
    </div>
    <p class="sub">
      <template v-if="plan.setup">{{ plan.setup }} · </template>вход {{ plan.level != null ? price(plan.level) : plan.entry_text || '–' }}<template v-if="plan.stop != null"> · стоп {{ price(plan.stop) }}</template><template v-if="when"> · {{ when }}</template>
    </p>
  </li>
</template>

<style scoped>
.row { padding: var(--space-3) 0; border-top: 1px solid var(--line); }
.row:first-child { border-top: 0; padding-top: 0; }
.head { display: flex; align-items: center; gap: var(--space-2); }
.sym { font-size: 17px; font-weight: 700; letter-spacing: -0.01em; }
.reason { font-size: 12px; color: var(--muted); }
.pnl { margin-left: auto; font-size: 17px; font-weight: 700; font-variant-numeric: tabular-nums; color: var(--muted); }
.pnl small { font-size: 11px; font-weight: 400; color: var(--muted); }
.pnl.up { color: var(--pos); }
.pnl.down { color: var(--neg); }
.sub { margin: 2px 0 0; font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
</style>
