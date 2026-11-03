<script setup lang="ts">
import { computed, ref } from 'vue'
import {
  MemoryBlockState, N_HINT, REJECT_HINT, SMALL_HINT, SMALL_TEXT, condRu, effectHint, effectView, expiryHint, expiryText, lessonStatusHint, lessonStatusTone, lessonStatusWord, lessonText,
  srcHint, srcWord, stateText, type BlockState, type LessonRow, type LessonsDetail,
} from '@/entities/memory'
import { UiChip, UiSegment, UiTip } from '@/shared/ui'

/**
 * Уроки: что система считает закономерностью. У каждого урока статус, источник, n, эффект с 95% интервалом и срок; n < 30 помечено.
 * «Отклонить» (без диалога) есть только у действующих уроков из разметки: уроки мозга живут по своим правилам.
 */
const props = defineProps<{ detail: LessonsDetail | null; state: BlockState; now: number; busy: boolean }>()
const emit = defineEmits<{ reject: [lesson: LessonRow] }>()

type Filter = 'live' | 'all'
const filter = ref<Filter>('live')
const PAGE = 12
const limit = ref(PAGE)

const ok = computed(() => props.state === 'ok' && !!props.detail)
const all = computed(() => props.detail?.rows ?? [])
const live = computed(() => all.value.filter((l) => l.status === 'hyp' || l.status === 'confirmed'))
const list = computed(() => (filter.value === 'live' ? live.value : all.value))
const shown = computed(() => list.value.slice(0, limit.value))
const options = computed(() => [
  { value: 'live' as Filter, label: `Действующие ${live.value.length}` },
  { value: 'all' as Filter, label: `Все ${all.value.length}` },
])
function pick(v: Filter): void {
  filter.value = v
  limit.value = PAGE
}
const dbNote = computed(() => (props.detail && props.detail.db && props.detail.db !== 'ok' ? `Испытания и правила уроков сейчас не видны. ${stateText(props.detail.db, props.detail.db_reason, 'об испытаниях')}` : ''))
</script>

<template>
  <section class="ml" aria-labelledby="ml-h">
    <header class="ml-head">
      <h2 id="ml-h" class="ml-title">Уроки <span v-if="ok" class="cnt">{{ detail?.total }}</span></h2>
      <UiSegment v-if="ok && all.length" :model-value="filter" :options="options" label="Какие уроки показать" @update:model-value="pick" />
    </header>

    <MemoryBlockState v-if="!ok" :state="state" :reason="detail?.reason" what="об уроках" :rows="5" />
    <template v-else>
      <p v-if="dbNote" class="db" role="status">{{ dbNote }}</p>
      <div class="cols" aria-hidden="true">
        <span>Урок</span><span>Эффект</span><span>Срок</span><span />
      </div>
      <ul v-if="shown.length" class="rows">
        <li v-for="l in shown" :key="l.lesson_id ?? ''" class="row">
          <div class="l-main">
            <p class="l-text">{{ lessonText(l) }}</p>
            <p v-if="l.txt && l.cond_txt" class="l-cond">Условие: {{ condRu(l.cond_txt) }}</p>
            <p class="l-tags">
              <UiTip :tip="lessonStatusHint(l.status, l.reason_code)"><UiChip :tone="lessonStatusTone(l.status)">{{ lessonStatusWord(l.status) }}</UiChip></UiTip>
              <UiTip :tip="srcHint(l.src)"><UiChip>{{ srcWord(l.src) }}</UiChip></UiTip>
              <span class="mono">{{ l.lesson_id }}</span>
            </p>
          </div>
          <div class="l-eff">
            <UiTip :tip="effectHint(l)" class="eff-tip">
              <b class="val" :class="effectView(l.effect, l.claim?.metric).tone">{{ effectView(l.effect, l.claim?.metric).value }}</b>
            </UiTip>
            <span class="sub">{{ effectView(l.effect, l.claim?.metric).interval }}</span>
            <span class="sub">
              <UiTip dotted :tip="N_HINT">{{ effectView(l.effect, l.claim?.metric).n }}</UiTip>
              <UiTip v-if="effectView(l.effect, l.claim?.metric).small" :tip="SMALL_HINT"><UiChip tone="warn">{{ SMALL_TEXT }}</UiChip></UiTip>
            </span>
          </div>
          <div class="l-term">
            <UiTip dotted :tip="expiryHint(l, now)">{{ expiryText(l.expires_ts, now) }}</UiTip>
          </div>
          <div class="l-act">
            <UiTip v-if="l.can_reject" :tip="REJECT_HINT" label="Что значит «отклонить»" class="q">?</UiTip>
            <button v-if="l.can_reject" type="button" class="rej" :disabled="busy" :aria-label="`Отклонить урок ${l.lesson_id}`" @click="emit('reject', l)">Отклонить</button>
          </div>
        </li>
      </ul>
      <p v-else class="none">{{ filter === 'live' ? 'Действующих уроков нет: все прежние отклонены или устарели.' : 'Уроков нет.' }}</p>
      <button v-if="list.length > shown.length" type="button" class="more" @click="limit += PAGE">Показать ещё {{ Math.min(PAGE, list.length - shown.length) }} из {{ list.length - shown.length }}</button>
    </template>
  </section>
</template>

<style scoped>
.ml { display: grid; gap: var(--space-3); align-content: start; padding: var(--space-4); background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); min-width: 0; }
.ml-head { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: var(--space-2) var(--space-3); }
.ml-title { margin: 0; font-size: 15px; font-weight: 700; letter-spacing: -0.01em; }
.cnt { margin-left: var(--space-1); color: var(--muted); font-weight: 400; font-size: 13px; font-variant-numeric: tabular-nums; }
.mono { font-family: var(--font-mono); font-size: 11px; color: var(--muted); }
.db { margin: 0; padding: var(--space-2) var(--space-3); border-radius: var(--radius-sm); background: var(--panel2); font-size: 12px; line-height: 1.5; color: var(--muted); }
.none { margin: 0; font-size: 12px; color: var(--muted); }

.cols, .row { display: grid; grid-template-columns: minmax(0, 1fr) 170px 110px 132px; gap: var(--space-3); }
.cols { font-size: 11px; color: var(--muted); padding-bottom: 2px; }
.rows { list-style: none; margin: 0; padding: 0; display: grid; }
.row { align-items: start; padding: var(--space-3) 0; border-top: 1px solid var(--line); }
.l-main { min-width: 0; display: grid; gap: 4px; }
.l-text { margin: 0; font-size: 13px; line-height: 1.45; overflow-wrap: anywhere; }
.l-cond { margin: 0; font-size: 12px; line-height: 1.45; color: var(--muted); overflow-wrap: anywhere; }
.l-tags { margin: 0; display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-1) var(--space-2); }
.l-eff { display: grid; gap: 2px; justify-items: start; min-width: 0; }
.val { font-size: 15px; font-weight: 700; font-variant-numeric: tabular-nums; }
.val.pos { color: var(--pos); }
.val.neg { color: var(--neg); }
.val.muted { color: var(--text); }
.sub { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-1) var(--space-2); font-size: 11px; color: var(--muted); font-variant-numeric: tabular-nums; }
.l-term { font-size: 12px; color: var(--muted); padding-top: 2px; }
.l-act { display: flex; align-items: center; justify-content: flex-end; gap: var(--space-2); }
.q { width: 22px; height: 22px; justify-content: center; border: 1px solid var(--line2); border-radius: 50%; font-size: 11px; color: var(--muted); }

.rej, .more { min-height: 32px; padding: 0 var(--space-3); border-radius: var(--radius-sm); border: 1px solid var(--line2); background: transparent; color: var(--text); font-size: 12px; cursor: pointer; transition: border-color var(--dur-fast) ease, color var(--dur-fast) ease, transform 120ms var(--ease-out); }
.rej:disabled { opacity: 0.5; cursor: not-allowed; }
.rej:active:not(:disabled), .more:active { transform: scale(0.97); }
.more { justify-self: start; color: var(--muted); }
@media (hover: hover) and (pointer: fine) {
  .rej:not(:disabled):hover, .more:hover { border-color: var(--muted); color: var(--text); }
}

@media (max-width: 900px) {
  .cols { display: none; }
  .row { grid-template-columns: minmax(0, 1fr) auto; }
  .l-main { grid-column: 1 / -1; }
  .l-term { padding-top: 0; }
  .l-act { grid-column: 1 / -1; justify-content: flex-start; }
}
</style>
