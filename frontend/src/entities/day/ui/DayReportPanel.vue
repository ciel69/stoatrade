<script setup lang="ts">
import { computed } from 'vue'
import { dayLabel, dayModeWord, fixed, signed, stopSourceText, usd, verdictWord } from '@/shared/lib'
import { UiChip, UiDetail } from '@/shared/ui'
import { carryViews } from '../lib/day'
import type { DayReportReply } from '../model/types'

/**
 * Содержимое боковой панели «Итог дня»: результат (чистый PnL, комиссии, funding), число сделок и R, расход на модели, перенесённое, открытые вопросы, затем текст отчёта.
 * Текст отчёта (markdown) выводится как обычный текст, без разметки и HTML: всё, что в нём есть, попадает на экран буквально и ничего не исполняется.
 */
const props = defineProps<{ id: string; reply: DayReportReply | undefined }>()
defineEmits<{ close: [] }>()

const d = computed(() => (props.reply?.ok ? (props.reply.data ?? null) : null))
const title = computed(() => dayLabel(props.id))
const tone = computed(() => (d.value?.verdict === 'ok' ? 'ok' : d.value?.verdict === 'attention' ? 'warn' : d.value?.verdict === 'alarm' ? 'bad' : 'neutral'))
const carried = computed(() => carryViews(d.value?.carried ?? [], null))
const rows = computed(() => {
  const x = d.value
  if (!x) return []
  return [
    { k: 'Результат (чистый)', v: `${signed(x.pnl.net, 4)} USDT`, tone: x.pnl.net == null || x.pnl.net === 0 ? '' : x.pnl.net > 0 ? 'pos' : 'neg' },
    { k: 'Комиссии', v: `${fixed(x.pnl.fees, 4)} USDT`, tone: '' },
    { k: 'Фандинг (плата за удержание)', v: `${signed(x.pnl.funding, 4)} USDT`, tone: '' },
    { k: 'Сделок закрыто', v: `${x.trades_n}${x.open_trades_n ? `, ещё открыто: ${x.open_trades_n}` : ''}`, tone: '' },
    { k: 'Результат в R', v: signed(x.pnl.r_sum, 2), tone: '' },
    { k: 'Расход на модели', v: `${usd(x.cost.usd)}, вызовов: ${x.cost.calls}`, tone: '' },
    { k: 'Перенесено через сутки', v: String(x.carried.length), tone: '' },
    { k: 'Открытых вопросов', v: String(x.open_questions.length), tone: '' },
  ]
})
const how = computed(() => [d.value?.mode ? dayModeWord(d.value.mode) : '', stopSourceText(d.value?.stop_source)].filter(Boolean).join(' · '))
</script>

<template>
  <UiDetail :label="`Итог дня ${title}`" @close="$emit('close')">
    <template #title>
      <b class="ttl">Итог дня {{ title }}</b>
      <UiChip v-if="d?.verdict" :tone="tone">{{ verdictWord(d.verdict) }}</UiChip>
    </template>

    <template v-if="d">
      <p v-if="how" class="how">{{ how }}</p>
      <dl class="kv">
        <div v-for="r in rows" :key="r.k" class="r"><dt>{{ r.k }}</dt><dd :class="r.tone">{{ r.v }}</dd></div>
      </dl>
      <p v-if="d.narrative" class="narr">{{ d.narrative }}</p>

      <section v-if="d.close_failed" class="alert" role="alert">
        <b>Закрытие дня не подтверждено исполнителем.</b> Позиции остаются под защитой стопов на бирже, нужен ручной разбор.
      </section>

      <section v-if="carried.length" class="blk">
        <h3>Перенесено через сутки</h3>
        <ul class="list">
          <li v-for="x in carried" :key="x.key"><b>{{ x.title }}</b><span class="muted">{{ x.facts }}</span><span v-if="x.why" class="why">{{ x.why }}</span></li>
        </ul>
      </section>

      <section v-if="d.open_questions.length" class="blk">
        <h3>Открытые вопросы</h3>
        <ul class="list">
          <li v-for="(q, i) in d.open_questions" :key="q.id ?? i"><span>{{ q.text }}</span></li>
        </ul>
      </section>

      <section v-if="d.md" class="blk">
        <h3>Текст отчёта</h3>
        <pre class="md" tabindex="0">{{ d.md }}</pre>
      </section>
    </template>
    <p v-else-if="reply && !reply.ok" class="empty">Отчёта по этому дню нет. Он появляется, когда день закрыт.</p>
    <div v-else class="skel" aria-busy="true" />
  </UiDetail>
</template>

<style scoped>
.ttl { font-size: 16px; letter-spacing: -0.01em; }
.how { margin: 0; font-size: 12px; color: var(--muted); }
.kv { margin: 0; display: grid; }
.r { display: flex; align-items: baseline; justify-content: space-between; gap: var(--space-3); padding: var(--space-2) 0; border-top: 1px solid var(--line); font-size: 13px; }
.r:first-child { border-top: 0; }
dt { color: var(--muted); }
dd { margin: 0; font-weight: 600; text-align: right; font-variant-numeric: tabular-nums; }
dd.pos { color: var(--pos); }
dd.neg { color: var(--neg); }
.narr { margin: 0; font-size: 13px; line-height: 1.55; text-wrap: pretty; }
.alert { padding: var(--space-3); border: 1px solid color-mix(in srgb, var(--neg) 55%, var(--line)); border-radius: var(--radius-sm); background: color-mix(in srgb, var(--neg) 9%, var(--panel)); font-size: 13px; line-height: 1.5; }
.alert b { color: var(--neg); }
.blk { display: grid; gap: var(--space-2); }
.blk h3 { margin: 0; font-size: 13px; font-weight: 600; }
.list { list-style: none; margin: 0; padding: 0; display: grid; }
.list li { display: flex; flex-wrap: wrap; align-items: baseline; gap: var(--space-1) var(--space-3); padding: var(--space-2) 0; border-top: 1px solid var(--line); font-size: 13px; }
.list li:first-child { border-top: 0; }
.muted { color: var(--muted); font-size: 12px; }
.why { flex-basis: 100%; color: var(--muted); font-size: 12px; line-height: 1.5; }
.md { margin: 0; padding: var(--space-3); background: var(--panel2); border-radius: var(--radius-sm); font-family: var(--font-mono); font-size: 12px; line-height: 1.55; white-space: pre-wrap; overflow-wrap: anywhere; max-height: 360px; overflow-y: auto; }
.md:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.empty { margin: 0; font-size: 13px; line-height: 1.5; color: var(--muted); }
.skel { height: 200px; border-radius: var(--radius-sm); background: var(--panel2); }
</style>
