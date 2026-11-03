<script setup lang="ts">
import { computed } from 'vue'
import { blockView, type LearningBlock, type VersionsBlock } from '@/entities/learning'
import { UiChip, UiTip } from '@/shared/ui'
import { DIFF_TIP, R_TIP, VERSIONS_INTRO, VIOL_TIP, pairViews, preR2Text, versionsSummary } from '../lib/versions'

/**
 * Блок «Версии мозга до/после» (R2.6, Requirement 14.2): для каждой смены правил, модели или промпта — средний R, сделки и нарушения «до» и «после»,
 * разница с 95% интервалом и вердикт словами. Вердикт сервера: «данных мало» при n < 100 с любой стороны, иначе «хуже» или «не хуже».
 */
const props = withDefaults(defineProps<{ block: LearningBlock; computingForS?: number }>(), { computingForS: 0 })

const view = computed(() => blockView(props.block, props.computingForS))
const b = computed(() => (view.value.kind === 'data' ? (props.block as VersionsBlock) : null))
const rows = computed(() => (b.value ? pairViews(b.value) : []))
const summary = computed(() => versionsSummary(rows.value))
const pre = computed(() => preR2Text(b.value?.n_pre_r2))
</script>

<template>
  <section class="vr" aria-labelledby="vr-h">
    <header class="head">
      <h2 id="vr-h" class="title">{{ block.title }}</h2>
      <UiChip v-if="summary" :tone="summary.tone">{{ summary.word }}</UiChip>
    </header>

    <p v-if="!b" class="state" role="status">{{ view.message }}</p>

    <template v-else>
      <p class="note">{{ VERSIONS_INTRO }}</p>

      <ul class="pairs">
        <li v-for="r in rows" :key="r.key" class="pair">
          <div class="p-head">
            <div class="p-name">
              <b class="p-title">{{ r.title }}</b>
              <span class="meta">{{ r.meta }}</span>
            </div>
            <UiTip :tip="r.verdictTip"><UiChip :tone="r.tone">{{ r.verdict }}</UiChip></UiTip>
          </div>

          <dl class="sides">
            <div v-for="side in (['before', 'after'] as const)" :key="side" class="side">
              <dt class="s-h">{{ side === 'before' ? 'До' : 'После' }} <span class="meta">{{ r[side].n }}</span></dt>
              <dd class="s-v">
                <span class="line">
                  <UiTip dotted :tip="R_TIP" class="lab">R</UiTip>
                  <b class="val" :class="r[side].r.tone">{{ r[side].r.value }}</b>
                  <span v-if="r[side].r.interval" class="meta">{{ r[side].r.interval }}</span>
                </span>
                <span class="line">
                  <UiTip dotted :tip="VIOL_TIP" class="lab">нарушения</UiTip>
                  <b class="val">{{ r[side].viol.value }}</b>
                  <span v-if="r[side].viol.interval" class="meta">{{ r[side].viol.interval }}</span>
                </span>
                <span class="meta">{{ r[side].violBase }}</span>
                <UiChip v-if="r[side].mark" tone="warn" class="mark">{{ r[side].mark }}</UiChip>
              </dd>
            </div>
            <div class="side diff">
              <dt class="s-h"><UiTip dotted :tip="DIFF_TIP">Разница</UiTip></dt>
              <dd class="s-v">
                <span class="line"><b class="val" :class="r.diff.tone">{{ r.diff.value }}</b><span v-if="r.diff.interval" class="meta">{{ r.diff.interval }}<template v-if="r.level">, {{ r.level }}</template></span></span>
                <span v-if="r.p" class="meta">{{ r.p }}</span>
              </dd>
            </div>
          </dl>
        </li>
      </ul>

      <p v-if="pre" class="foot">{{ pre }}</p>
    </template>
  </section>
</template>

<style scoped>
.vr { display: grid; gap: var(--space-3); background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: var(--space-4); min-width: 0; }
.vr > * { min-width: 0; }
.head { display: flex; align-items: center; gap: var(--space-3); flex-wrap: wrap; }
.title { margin: 0; font-size: 14px; font-weight: 600; }
.state { margin: 0; color: var(--muted); font-size: 13px; line-height: 1.5; }
.note { margin: 0; color: var(--muted); font-size: 12px; line-height: 1.5; max-width: 72ch; }
.meta { font-size: 11px; color: var(--muted); font-variant-numeric: tabular-nums; line-height: 1.4; }
.foot { margin: 0; font-size: 11px; color: var(--muted); line-height: 1.5; }

.pairs { list-style: none; margin: 0; padding: 0; display: grid; }
.pair { display: grid; gap: var(--space-2); padding: var(--space-3) 0; border-top: 1px solid var(--line); min-width: 0; }
.pair:last-child { padding-bottom: 0; }
.p-head { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--space-2) var(--space-3); flex-wrap: wrap; }
.p-name { display: grid; gap: 2px; min-width: 0; }
.p-title { font-size: 13px; font-weight: 600; overflow-wrap: anywhere; }

.sides { margin: 0; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)) minmax(0, 0.9fr); gap: var(--space-3); }
.side { display: grid; align-content: start; gap: 4px; min-width: 0; }
.s-h { font-size: 12px; font-weight: 600; display: flex; align-items: baseline; gap: var(--space-2); flex-wrap: wrap; }
.s-v { margin: 0; display: grid; gap: 4px; justify-items: start; min-width: 0; }
.line { display: flex; flex-wrap: wrap; align-items: baseline; gap: 2px var(--space-2); }
.lab { font-size: 11px; color: var(--muted); }
.val { font-size: 14px; font-weight: 700; font-variant-numeric: tabular-nums; }
.val.pos { color: var(--pos); }
.val.neg { color: var(--neg); }
.diff { padding-left: var(--space-3); border-left: 1px solid var(--line); }

@media (max-width: 720px) {
  .sides { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .diff { grid-column: 1 / -1; padding-left: 0; border-left: 0; padding-top: var(--space-2); border-top: 1px dashed var(--line); }
}
</style>
