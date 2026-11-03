<script setup lang="ts">
import { computed } from 'vue'
import { blockView, type LearningBlock, type LessonsBlock } from '@/entities/learning'
import { N_HINT, SMALL_HINT } from '@/entities/memory'
import { UiChip, UiTip } from '@/shared/ui'
import { BEST_TIP, SRC_LABELS, bestNone, bestView, envText, funnelSteps, lessonsCount, srcTip, statusRows, weekRows, weeksSummary } from '../lib/lessons'

/**
 * Блок «Уроки» (R2.6, Requirement 14.1): воронка цикла, уроки по статусам и источникам, лучший урок с n и 95% интервалом, недельный ряд.
 * Пока таблиц цикла нет, блок приходит со статусом stage и показывается страницей среди «следующих этапов». Только показ готовых чисел сервера.
 */
const props = withDefaults(defineProps<{ block: LearningBlock; computingForS?: number }>(), { computingForS: 0 })

const view = computed(() => blockView(props.block, props.computingForS))
const b = computed(() => (view.value.kind === 'data' ? (props.block as LessonsBlock) : null))
const steps = computed(() => (b.value ? funnelSteps(b.value) : []))
const statuses = computed(() => (b.value ? statusRows(b.value) : []))
const best = computed(() => (b.value ? bestView(b.value.best, b.value.best_min_n ?? 30) : null))
const weeks = computed(() => (b.value ? weekRows(b.value.weeks) : []))
const minN = computed(() => b.value?.best_min_n ?? 30)
</script>

<template>
  <section class="ls" aria-labelledby="ls-h">
    <header class="head">
      <h2 id="ls-h" class="title">{{ block.title }}</h2>
      <UiChip v-if="b">{{ lessonsCount(b.n_lessons) }}</UiChip>
    </header>

    <p v-if="!b" class="state" role="status">{{ view.message }}</p>

    <template v-else>
      <p class="note">{{ envText(b.env_norm) }}</p>

      <ol class="funnel" aria-label="Воронка цикла: сколько дошло до каждого шага">
        <li v-for="s in steps" :key="s.key" class="st">
          <b class="num">{{ s.value }}</b>
          <UiTip dotted :tip="s.tip" class="lbl">{{ s.label }}</UiTip>
          <span v-if="s.sub" class="sub">{{ s.sub }}</span>
        </li>
      </ol>

      <div class="best" :class="{ none: !best }">
        <h3 class="sub-h"><UiTip dotted :tip="BEST_TIP">Самый сильный урок</UiTip></h3>
        <template v-if="best">
          <p class="best-t">{{ best.text }}</p>
          <p class="tags">
            <UiTip :tip="best.statusTip"><UiChip :tone="best.statusTone">{{ best.status }}</UiChip></UiTip>
            <UiTip :tip="best.srcTip"><UiChip>{{ best.src }}</UiChip></UiTip>
            <span class="mono">{{ best.id }}</span>
          </p>
          <div class="eff">
            <UiTip :tip="best.tip"><b class="val" :class="best.diff.tone">{{ best.diff.value }}</b></UiTip>
            <span class="meta">{{ best.diff.interval }}<template v-if="best.diff.interval">, 95%</template></span>
            <span class="meta">
              <UiTip dotted :tip="N_HINT">{{ best.diff.n }}</UiTip>
              <UiTip v-if="best.small" :tip="SMALL_HINT"><UiChip tone="warn">{{ best.small }}</UiChip></UiTip>
            </span>
          </div>
        </template>
        <p v-else class="state">{{ bestNone(minN) }}</p>
      </div>

      <div v-if="statuses.length" class="wrap" role="region" aria-label="Уроки по статусам и источникам" tabindex="0">
        <table class="tab">
          <thead>
            <tr>
              <th scope="col">Статус</th>
              <th scope="col" class="n"><UiTip dotted :tip="srcTip('brain')">{{ SRC_LABELS.brain }}</UiTip></th>
              <th scope="col" class="n"><UiTip dotted :tip="srcTip('labels')">{{ SRC_LABELS.labels }}</UiTip></th>
              <th scope="col" class="n">Всего</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in statuses" :key="r.key">
              <th scope="row"><UiTip :tip="r.tip"><UiChip :tone="r.tone">{{ r.word }}</UiChip></UiTip></th>
              <td class="n" :class="{ zero: !r.brain }">{{ r.brain }}</td>
              <td class="n" :class="{ zero: !r.labels }">{{ r.labels }}</td>
              <td class="n tot">{{ r.total }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <details v-if="weeks.length" class="tbl">
        <summary>По неделям <span class="meta">{{ weeksSummary(weeks) }}</span></summary>
        <div class="wrap" role="region" aria-label="Уроки по неделям" tabindex="0">
          <table class="tab">
            <thead>
              <tr>
                <th scope="col">Неделя</th>
                <th scope="col" class="n">Новых</th>
                <th scope="col" class="n">из них {{ SRC_LABELS.brain }}</th>
                <th scope="col" class="n">Подтверждено</th>
                <th scope="col" class="n">Отклонено</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="w in weeks" :key="w.key">
                <th scope="row">{{ w.label }}<span v-if="w.partial" class="meta"> · идёт</span></th>
                <td class="n" :class="{ zero: !w.created }">{{ w.created }}</td>
                <td class="n" :class="{ zero: !w.brain }">{{ w.brain }}</td>
                <td class="n" :class="{ zero: !w.confirmed }">{{ w.confirmed }}</td>
                <td class="n" :class="{ zero: !w.rejected }">{{ w.rejected }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </details>
    </template>
  </section>
</template>

<style scoped>
.ls { display: grid; gap: var(--space-3); background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: var(--space-4); min-width: 0; }
.ls > * { min-width: 0; }         /* элемент сетки не растягивается шириной таблицы: таблица прокручивается в своей обёртке */
.head { display: flex; align-items: center; gap: var(--space-3); flex-wrap: wrap; }
.title { margin: 0; font-size: 14px; font-weight: 600; }
.state { margin: 0; color: var(--muted); font-size: 13px; line-height: 1.5; }
.note { margin: 0; color: var(--muted); font-size: 12px; line-height: 1.5; max-width: 72ch; }
.sub-h { margin: 0; font-size: 12px; font-weight: 600; color: var(--muted); }
.meta { font-size: 11px; color: var(--muted); font-weight: 400; font-variant-numeric: tabular-nums; }
.sub { font-size: 11px; color: var(--muted); font-variant-numeric: tabular-nums; }
.mono { font-family: var(--font-mono); font-size: 11px; color: var(--muted); }

/* воронка: пять станций, числа крупно; без своего движения (оно у блока «Гипотезы») */
.funnel { list-style: none; margin: 0; padding: var(--space-3) 0; display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: var(--space-3); border-block: 1px solid var(--line); }
.st { display: grid; justify-items: start; align-content: start; gap: 2px; min-width: 0; }
.num { font-size: 22px; font-weight: 700; line-height: 1.15; letter-spacing: -0.02em; font-variant-numeric: tabular-nums; }
.lbl { font-size: 12px; color: var(--muted); }

.best { display: grid; gap: var(--space-2); min-width: 0; }
.best-t { margin: 0; font-size: 13px; line-height: 1.45; overflow-wrap: anywhere; max-width: 72ch; }
.tags { margin: 0; display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-1) var(--space-2); }
.eff { display: flex; flex-wrap: wrap; align-items: baseline; gap: var(--space-1) var(--space-3); }
.eff .meta { display: inline-flex; flex-wrap: wrap; align-items: center; gap: var(--space-1) var(--space-2); }
.val { font-size: 17px; font-weight: 700; font-variant-numeric: tabular-nums; }
.val.pos { color: var(--pos); }
.val.neg { color: var(--neg); }

.wrap { overflow-x: auto; min-width: 0; }
.tab { width: 100%; border-collapse: collapse; font-size: 12px; font-variant-numeric: tabular-nums; }
.tab th, .tab td { padding: var(--space-1) var(--space-2); border-top: 1px solid var(--line); text-align: left; font-weight: 400; vertical-align: middle; }
.tab thead th { border-top: 0; color: var(--muted); white-space: nowrap; }
.tab .n { text-align: right; }
.tab .zero { color: var(--muted); }
.tab .tot { font-weight: 600; }

details.tbl > summary { cursor: pointer; font-size: 12px; font-weight: 600; padding: var(--space-1) 0; border-radius: var(--radius-sm); list-style-position: inside; }
details.tbl > summary .meta { margin-left: var(--space-2); }
details.tbl > summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
details.tbl[open] > summary { margin-bottom: var(--space-2); }

@media (max-width: 640px) {
  .funnel { grid-template-columns: repeat(3, minmax(0, 1fr)); row-gap: var(--space-3); }
}
</style>
