<script setup lang="ts">
import { computed } from 'vue'
import { UiChip, UiTip } from '@/shared/ui'
import { ageText, fixed, signed } from '@/shared/lib'
import { ideaSideHint, noteRu, outcomeHint, statusHint, zoneHint } from '../lib/hints'
import { lastOutcome, STATUS_RU } from '../lib/idea'
import type { IdeaItem } from '../model/types'

/**
 * Строка идеи, у которой есть исход: «стала планом» (результат сделки, если дошло) и «не сработала» (что было бы по стороне идеи после издержек).
 * Одна строка: монета, сторона, статус, когда записана и результат справа; тезис ниже одной строкой; клик открывает подробности боковой панелью.
 */
const props = defineProps<{ idea: IdeaItem; now: number; selected: boolean }>()
const emit = defineEmits<{ select: [] }>()

const last = computed(() => lastOutcome(props.idea.outcomes))
const tr = computed(() => props.idea.trade)
const tone = (v: number | null | undefined) => (v == null || v === 0 ? '' : v > 0 ? 'up' : 'down')
const statusTone = computed(() => (props.idea.status === 'taken' || props.idea.status === 'planned' ? 'ok' : 'neutral'))
const note = computed(() => noteRu(props.idea.note))
</script>

<template>
  <li class="row" :class="{ sel: selected }">
    <button type="button" class="hit" :aria-pressed="selected" :aria-label="`${idea.sym}: открыть идею`" @click="emit('select')" />
    <div class="body">
      <span class="head">
        <span class="sym">{{ idea.sym }}</span>
        <UiTip :tip="ideaSideHint(idea.side)"><UiChip>{{ idea.side === 'long' ? 'лонг' : idea.side === 'short' ? 'шорт' : '?' }}</UiChip></UiTip>
        <UiTip :tip="statusHint(idea.status, STATUS_RU[idea.status] ?? idea.status)"><UiChip :tone="statusTone">{{ STATUS_RU[idea.status] ?? idea.status }}</UiChip></UiTip>
        <UiTip v-if="idea.zone" :tip="zoneHint"><UiChip tone="warn">у уровня</UiChip></UiTip>
        <span class="when">{{ ageText(idea.ts_epoch, now) }}</span>
        <span class="res">
          <template v-if="idea.status === 'planned'"><span class="muted">план {{ idea.plan_id }}</span></template>
          <template v-else-if="idea.status === 'taken'">
            <template v-if="tr">
              <UiChip v-if="tr.status === 'open'" tone="accent">в сделке</UiChip>
              <b :class="tone(tr.pnl)">{{ tr.pnl != null ? signed(tr.pnl, 3) : '–' }}</b>
              <span v-if="tr.r != null" class="muted"> {{ fixed(tr.r, 1) }}R</span>
            </template>
            <span v-else class="muted">сделка не найдена</span>
          </template>
          <template v-else-if="last"><UiTip :tip="outcomeHint(last.h)"><b :class="tone(last.v)">{{ signed(last.v) }}%</b></UiTip><span class="muted"> {{ last.h }}м</span></template>
          <span v-else class="muted">ждём замер</span>
        </span>
      </span>
      <span v-if="note" class="note">{{ note }}</span>
    </div>
  </li>
</template>

<style scoped>
.row { position: relative; padding: var(--space-3) 0; border-top: 1px solid var(--line); }
.row:first-child { border-top: 0; padding-top: 0; }
.row.sel { background: color-mix(in srgb, var(--accent) 7%, transparent); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent) 35%, transparent); border-radius: var(--radius-sm); padding-inline: var(--space-3); }
/* Вся строка кликабельна одной кнопкой под содержимым: внутри подсказки-кнопки, вложенные кнопки недопустимы */
.hit { position: absolute; inset: 0; width: 100%; padding: 0; border: 0; background: none; cursor: pointer; border-radius: var(--radius-sm); }
.hit:focus-visible { outline: 2px solid var(--accent); outline-offset: 4px; }
.body { position: relative; pointer-events: none; }
.body :deep(.tt) { pointer-events: auto; }
.head { display: flex; align-items: center; flex-wrap: wrap; gap: var(--space-2); }
.sym { font-size: 15px; font-weight: 700; }
.when { margin-left: auto; font-size: 12px; color: var(--muted); }
.res { min-width: 84px; text-align: right; font-size: 13px; font-variant-numeric: tabular-nums; }
.up { color: var(--pos); }
.down { color: var(--neg); }
.muted { color: var(--muted); font-size: 12px; margin-left: 4px; }
.note { display: block; margin-top: 4px; font-size: 12px; color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
@media (hover: hover) and (pointer: fine) {
  .row:hover .sym { color: var(--accent); }
}
</style>
