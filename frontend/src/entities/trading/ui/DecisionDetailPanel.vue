<script setup lang="ts">
import { computed } from 'vue'
import { UiChip, UiDetail } from '@/shared/ui'
import { price, signed } from '@/shared/lib'
import { actorKey, humanAction, planShort } from '../lib/decisions'
import type { DecisionItem } from '../model/types'

/** Боковая панель решения: что сделано и почему, параметры, основание, ответ, идентификаторы записи (номер, хеш, link_id, action_id). */
const props = defineProps<{ decision: DecisionItem }>()
defineEmits<{ close: [] }>()

const ACTOR_RU: Record<string, string> = { brain: 'мозг', exec: 'исполнитель', owner: 'владелец', system: 'система', rule: 'правило' }
const KIND_RU: Record<string, string> = { cmd: 'команда', accept: 'принято', reject: 'отказ', transition: 'переход', intent: 'намерение', result: 'результат', rule: 'правило', skip: 'пропуск', protect: 'защита', reconcile: 'сверка', event: 'событие', note: 'заметка' }
const fullFmt = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
const flat = (o: Record<string, unknown>): string => Object.entries(o).map(([k, v]) => `${k}=${v}`).join(' · ')

const res = computed<{ text: string; tone: 'ok' | 'bad' | 'warn' | 'neutral' }>(() => {
  const d = props.decision
  if (d.denied) return { text: `отказ ${d.code ?? ''}`.trim(), tone: 'bad' }
  if (d.idle) return { text: 'ждёт', tone: 'neutral' }
  if (d.noop) return { text: `пропуск ${d.code ?? ''}`.trim(), tone: 'warn' }
  if (d.ok === true) return { text: 'OK', tone: 'ok' }
  return { text: KIND_RU[d.kind] ?? d.kind, tone: 'neutral' }
})
const rows = computed<Array<[string, string]>>(() => {
  const d = props.decision
  const out: Array<[string, string]> = [
    ['Номер · вид', `#${d.seq} · ${KIND_RU[d.kind] ?? d.kind} · ${d.what}`],
    ['Время', `${d.t != null ? fullFmt.format(new Date(d.t * 1000)) : d.ts.slice(11, 19)}${d.ts ? ` (UTC ${d.ts})` : ''}`],
    ['Кто решил', d.actor],
    ['План', d.plan ? `${d.plan}${d.rev != null ? ` · r${d.rev}` : ''}${d.mode ? ` · ${d.mode}` : ''}` : '–'],
    ['Причина', d.why || '–'],
  ]
  if (Object.keys(d.args).length) out.push(['Параметры', flat(d.args)])
  if (Object.keys(d.basis).length) out.push(['Основание', flat(d.basis)])
  if (d.px != null || d.r != null) out.push(['Цена · R на момент', `${d.px != null ? price(d.px) : '–'} · ${d.r != null ? signed(d.r) + 'R' : '–'}`])
  if (Object.keys(d.res).length) out.push(['Ответ', flat(d.res)])
  if (d.link_id) out.push(['link_id', d.link_id])
  if (d.exch_ms != null) out.push(['Ответ биржи', `${d.exch_ms} мс`])
  if (d.action_id) out.push(['action_id', d.action_id])
  if (d.h) out.push(['Хеш записи', d.h])
  return out
})
</script>

<template>
  <UiDetail :label="`Решение ${decision.seq}`" @close="$emit('close')">
    <template #title>
      <UiChip :tone="actorKey(decision.actor) === 'brain' ? 'accent' : 'neutral'">{{ ACTOR_RU[actorKey(decision.actor)] ?? decision.actor }}</UiChip>
      <b class="action">{{ humanAction(decision) }}</b>
      <UiChip :tone="res.tone">{{ res.text }}</UiChip>
      <span v-if="decision.plan" class="plan" :title="decision.plan">{{ planShort(decision.plan) }}</span>
    </template>
    <p v-if="decision.why" class="why">{{ decision.why }}</p>
    <dl class="kv"><template v-for="[k, v] in rows" :key="k"><dt>{{ k }}</dt><dd>{{ v }}</dd></template></dl>
  </UiDetail>
</template>

<style scoped>
.action { font-size: 14px; overflow-wrap: anywhere; }
.plan { font-size: 11px; color: var(--muted); }
.why { margin: 0; padding: var(--space-3); border-radius: var(--radius-sm); background: var(--panel2); font-size: 13px; line-height: 1.5; overflow-wrap: anywhere; }
.kv { display: grid; grid-template-columns: minmax(100px, 0.6fr) minmax(0, 2fr); gap: 5px var(--space-3); margin: 0; font-size: 12px; }
.kv dt { color: var(--muted); }
.kv dd { margin: 0; overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }
</style>
