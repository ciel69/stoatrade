<script setup lang="ts">
import { computed } from 'vue'
import { blockView, type HypsBlock, type LearningBlock } from '@/entities/learning'
import { useNow } from '@/shared/lib'
import { UiChip, UiTip } from '@/shared/ui'
import { HYPS_INTRO, HYPS_NONE, HYPS_PAPER, R_TIP, correctionText, familyViews, hypsSummary, refAgeText } from '../lib/hyps'

/**
 * Блок «Гипотезы» (R2, Requirement 19.6): по семействам гипотез бумажной торговли — прогресс n из 30, средний R с 95% интервалом, статус словами
 * и справка меток («что говорят тысячи сигналов»), если для семейства она есть. Справка — ориентир, статус не меняет.
 * Движение блока одно: полосы прогресса заполняются слева направо при появлении данных (лесенкой по 40 мс); при reduce-motion — сразу.
 */
const props = withDefaults(defineProps<{ block: LearningBlock; computingForS?: number }>(), { computingForS: 0 })
const now = useNow()

const view = computed(() => blockView(props.block, props.computingForS))
const b = computed(() => (view.value.kind === 'data' ? (props.block as HypsBlock) : null))
const rows = computed(() => (b.value ? familyViews(b.value) : []))
const summary = computed(() => (b.value ? hypsSummary(b.value) : null))
const message = computed(() => (props.block.status === 'no_data' && props.block.reason === 'no_trades' ? HYPS_NONE : view.value.message))
</script>

<template>
  <section class="hy" aria-labelledby="hy-h">
    <header class="head">
      <h2 id="hy-h" class="title">{{ block.title }}</h2>
      <UiChip v-if="summary" :tone="summary.tone">{{ summary.word }}</UiChip>
    </header>

    <p v-if="!b" class="state" role="status">{{ message }}</p>

    <template v-else>
      <p class="note">{{ HYPS_INTRO }} {{ HYPS_PAPER }}</p>

      <ul class="fams" aria-label="Гипотезы по семействам">
        <li v-for="(f, i) in rows" :key="f.key" class="fam" :style="{ '--i': Math.min(i, 8) }">
          <div class="f-name">
            <UiTip :tip="f.hint" class="nm">{{ f.name }}</UiTip>
            <UiTip :tip="f.statusTip"><UiChip :tone="f.tone">{{ f.status }}</UiChip></UiTip>
          </div>

          <div class="f-prog">
            <span class="bar" role="meter" :aria-valuenow="Math.round(f.progress.ratio * 100)" aria-valuemin="0" aria-valuemax="100" :aria-label="f.progress.label">
              <i :class="{ done: f.progress.done }" :style="{ transform: `scaleX(${f.progress.ratio})` }" />
            </span>
            <span class="meta" aria-hidden="true">{{ f.progress.text }}</span>
          </div>

          <div class="f-r">
            <span class="line">
              <UiTip dotted :tip="R_TIP" class="lab">R</UiTip>
              <b class="val" :class="f.r.tone">{{ f.r.value }}</b>
              <span v-if="f.r.interval" class="meta">{{ f.r.interval }}</span>
            </span>
            <span class="meta">
              <template v-if="f.r.n">{{ f.r.n }}</template><template v-if="f.win"> · {{ f.win }}</template><template v-if="f.p"> · {{ f.p }}</template>
            </span>
            <UiChip v-if="f.r.small && f.progress.ratio > 0" tone="warn">{{ f.r.small }}</UiChip>
          </div>

          <div v-if="f.ref" class="f-ref">
            <UiTip dotted :tip="f.ref.tip" class="ref-h">Что говорят тысячи сигналов</UiTip>
            <span class="line">
              <b class="val sm" :class="f.ref.r.tone">{{ f.ref.r.value }}</b>
              <span v-if="f.ref.r.interval" class="meta">{{ f.ref.r.interval }}</span>
              <span class="meta">{{ f.ref.n }}</span>
              <UiChip v-if="f.ref.small" tone="warn">{{ f.ref.small }}</UiChip>
            </span>
            <span class="meta">{{ f.ref.who }}</span>
          </div>
        </li>
      </ul>

      <p class="foot">{{ correctionText(b) }} {{ refAgeText(b.ref_ts, now) }}</p>
    </template>
  </section>
</template>

<style scoped>
.hy { display: grid; gap: var(--space-3); background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: var(--space-4); min-width: 0; }
.hy > * { min-width: 0; }
.head { display: flex; align-items: center; gap: var(--space-3); flex-wrap: wrap; }
.title { margin: 0; font-size: 14px; font-weight: 600; }
.state { margin: 0; color: var(--muted); font-size: 13px; line-height: 1.5; }
.note { margin: 0; color: var(--muted); font-size: 12px; line-height: 1.5; max-width: 80ch; text-wrap: pretty; }
.meta { font-size: 11px; color: var(--muted); font-variant-numeric: tabular-nums; line-height: 1.4; }
.foot { margin: 0; font-size: 11px; color: var(--muted); line-height: 1.5; }

.fams { list-style: none; margin: 0; padding: 0; display: grid; }
.fam {
  display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(0, 1fr) minmax(0, 1.2fr); grid-template-areas: "name prog r" "ref ref ref";
  gap: var(--space-2) var(--space-4); align-items: start; padding: var(--space-3) 0; border-top: 1px solid var(--line); min-width: 0;
}
.f-name { grid-area: name; display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-1) var(--space-2); min-width: 0; }
.nm { font-size: 13px; font-weight: 600; overflow-wrap: anywhere; }
.f-prog { grid-area: prog; display: grid; gap: 4px; padding-top: 6px; min-width: 0; }
.f-r { grid-area: r; display: grid; gap: 2px; justify-items: start; min-width: 0; }
.f-ref { grid-area: ref; display: grid; gap: 2px; justify-items: start; padding: var(--space-2) var(--space-3); border-radius: var(--radius-sm); background: var(--panel2); min-width: 0; }
.ref-h { font-size: 11px; font-weight: 600; color: var(--text); }
.line { display: flex; flex-wrap: wrap; align-items: baseline; gap: 2px var(--space-2); }
.lab { font-size: 11px; color: var(--muted); }
.val { font-size: 14px; font-weight: 700; font-variant-numeric: tabular-nums; }
.val.sm { font-size: 13px; }
.val.pos { color: var(--pos); }
.val.neg { color: var(--neg); }

/* полоса n из 30: заполнение — transform scaleX от левого края, без пересчёта вёрстки */
.bar { display: block; height: 6px; border-radius: 99px; background: var(--line); overflow: hidden; }
.bar i { display: block; height: 100%; border-radius: inherit; background: var(--accent); transform-origin: left center; transition: transform 420ms var(--ease-out); transition-delay: calc(var(--i, 0) * 40ms + 60ms); }
.bar i.done { background: var(--pos); }
@starting-style {
  .bar i { transform: scaleX(0); }
}
@media (prefers-reduced-motion: reduce) {
  .bar i { transition: none; }
}

@media (max-width: 720px) {
  .fam { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); grid-template-areas: "name name" "prog r" "ref ref"; }
}
@media (max-width: 420px) {
  .fam { grid-template-columns: minmax(0, 1fr); grid-template-areas: "name" "prog" "r" "ref"; }
}
</style>
