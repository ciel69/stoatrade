<script setup lang="ts">
import { computed, ref } from 'vue'
import { blockView, estView, type LearningBlock } from '@/entities/learning'
import {
  LEARN_BLOCK_ERROR, LEARN_RESULT_ALL, LEARN_RESULT_CHART_EMPTY, LEARN_RESULT_SERIES, LEARN_RESULT_SIGNAL, LEARN_RESULT_TABLE_CLOSE, LEARN_RESULT_TABLE_HEAD, LEARN_RESULT_TABLE_OPEN, LEARN_RESULT_WINDOW,
  LEARN_RESULT_WINDOW_LABEL, LEARN_RESULT_X,
} from '@/shared/lib'
import { UiBandChart, UiCard, UiSegment } from '@/shared/ui'
import { asResult, chartPoints, statsOf, tableRows, whereOfZero, windowKeys } from '../lib/result'

/**
 * Блок «Результат»: скользящий средний R с лентой интервала и нулевой линией, окна 50 и 200 сделок, под ним четыре числа окна (средний R, доля прибыльных,
 * профит-фактор, доля издержек), у каждого n и интервал либо пометка «расчётная величина»; «мало данных» подписано словами. Только чтение:
 * единственное действие это переключатель окна и таблица-альтернатива графику.
 */
const props = defineProps<{ block: LearningBlock }>()

const open = ref(false)
const onToggle = (e: Event): void => { open.value = (e.target as HTMLDetailsElement).open }
const picked = ref('50')
const bv = computed(() => blockView(props.block))
const res = computed(() => (bv.value.kind === 'data' ? asResult(props.block) : null))
const keys = computed(() => (res.value ? windowKeys(res.value) : []))
const win = computed(() => (keys.value.includes(picked.value) ? picked.value : (keys.value[0] ?? '50')))
const ws = computed(() => res.value?.windows[win.value] ?? null)
const options = computed(() => keys.value.map((w) => ({ value: w, label: LEARN_RESULT_WINDOW(w) })))
const points = computed(() => chartPoints(res.value?.series?.[win.value]))
const stats = computed(() => statsOf(ws.value))
const where = computed(() => whereOfZero(ws.value, res.value?.signal?.[win.value]))
const all = computed(() => {
  const a = res.value?.all
  if (!a || !a.r) return ''
  const e = estView(a.r, 'r')
  return [e.value, e.interval, e.n, a.small ? e.small : ''].filter(Boolean).join(', ')
})
const rows = computed(() => tableRows(points.value))
const message = computed(() => bv.value.message || (bv.value.kind === 'data' && !res.value ? LEARN_BLOCK_ERROR : ''))
</script>

<template>
  <UiCard class="res" :title="block.title" as="section">
    <template v-if="options.length > 1" #head>
      <UiSegment v-model="picked" :options="options" :label="LEARN_RESULT_WINDOW_LABEL" />
    </template>

    <p v-if="!res" class="msg" role="status">{{ message }}</p>

    <template v-else>
      <UiBandChart :points="points" :label="LEARN_RESULT_SERIES(win)" unit="R" :x-label="LEARN_RESULT_X" :height="148" :empty-text="LEARN_RESULT_CHART_EMPTY" signed />

      <p class="sig" :class="`w-${where}`">
        <svg class="gl" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false">
          <path v-if="where === 'above'" d="M8 13V3M4 7l4-4 4 4" />
          <path v-else-if="where === 'below'" d="M8 3v10M4 9l4 4 4-4" />
          <path v-else-if="where === 'zero'" d="M3 6h10M3 10h10" />
          <path v-else d="M4 8h8" />
        </svg>
        {{ LEARN_RESULT_SIGNAL[where] }}
      </p>

      <dl class="stats">
        <div v-for="s in stats" :key="s.id" class="stat">
          <dt>{{ s.label }}<span class="hint">{{ s.hint }}</span></dt>
          <dd class="val" :class="s.tone">{{ s.value }}</dd>
          <dd v-if="s.interval" class="sub">{{ s.interval }}</dd>
          <dd class="sub">{{ s.n }}</dd>
          <dd v-if="s.note" class="note">{{ s.note }}</dd>
        </div>
      </dl>

      <p v-if="all" class="all"><span>{{ LEARN_RESULT_ALL }}:</span> <span class="num">{{ all }}</span></p>

      <details v-if="rows.length" class="tbl" @toggle="onToggle">
        <summary class="tsum">{{ open ? LEARN_RESULT_TABLE_CLOSE : LEARN_RESULT_TABLE_OPEN }}</summary>
        <div class="scroll" tabindex="0" role="region" :aria-label="LEARN_RESULT_SERIES(win)">
          <table>
            <thead>
              <tr><th>{{ LEARN_RESULT_TABLE_HEAD.x }}</th><th>{{ LEARN_RESULT_TABLE_HEAD.r }}</th><th>{{ LEARN_RESULT_TABLE_HEAD.interval }}</th><th>{{ LEARN_RESULT_TABLE_HEAD.n }}</th></tr>
            </thead>
            <tbody>
              <tr v-for="r in rows" :key="r.x"><td>{{ r.x }}</td><td>{{ r.r }}</td><td>{{ r.interval }}</td><td>{{ r.n }}</td></tr>
            </tbody>
          </table>
        </div>
      </details>
    </template>
  </UiCard>
</template>

<style scoped>
.res { min-width: 0; display: grid; gap: var(--space-3); }
.res :deep(.head) { margin-bottom: 0; flex-wrap: wrap; }
.msg { margin: 0; padding: var(--space-4) 0; font-size: 14px; color: var(--muted); }

.sig { margin: 0; display: flex; align-items: center; gap: var(--space-2); font-size: 12px; color: var(--muted); }
.gl { flex: none; fill: none; stroke: currentColor; stroke-width: 1.7; stroke-linecap: round; stroke-linejoin: round; }
.sig.w-above { color: var(--pos); font-weight: 600; }
.sig.w-below { color: var(--neg); font-weight: 600; }

.stats { margin: 0; display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: var(--space-3) var(--space-5); padding-top: var(--space-3); border-top: 1px solid var(--line); }
.stat { display: grid; gap: 2px; align-content: start; min-width: 0; }
.stat dt { font-size: 12px; color: var(--muted); display: grid; }
.hint { font-size: 11px; }        /* без прозрачности: приглушённый цвет уже даёт иерархию, а opacity роняла контраст светлой темы ниже 4.5:1 */
.stat dd { margin: 0; font-variant-numeric: tabular-nums; }
.val { font-size: 20px; font-weight: 700; line-height: 1.3; letter-spacing: -0.01em; text-wrap: balance; }
.val.pos { color: var(--pos); }
.val.neg { color: var(--neg); }
.sub { font-size: 12px; color: var(--muted); }
.note { font-size: 11px; color: var(--warn); }

.all { margin: 0; font-size: 12px; color: var(--muted); }
.num { font-variant-numeric: tabular-nums; color: var(--text); }

.tsum { display: inline-block; padding: var(--space-1) 0; font-size: 12px; color: var(--muted); cursor: pointer; border-radius: var(--radius-sm); transition: color var(--dur-fast) ease; }
.tsum:focus-visible, .scroll:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.scroll { margin-top: var(--space-2); max-height: 220px; overflow: auto; border: 1px solid var(--line); border-radius: var(--radius-sm); }
table { width: 100%; border-collapse: collapse; font-size: 12px; font-variant-numeric: tabular-nums; }
th, td { padding: 4px var(--space-3); text-align: left; white-space: nowrap; border-bottom: 1px solid var(--line); }
th { position: sticky; top: 0; background: var(--panel2); font-weight: 600; color: var(--muted); }
tbody tr:last-child td { border-bottom: 0; }

@media (hover: hover) and (pointer: fine) {
  .tsum:hover { color: var(--text); }
}
@media (max-width: 520px) {
  .stats { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--space-3); }
  .val { font-size: 18px; }
}
</style>
