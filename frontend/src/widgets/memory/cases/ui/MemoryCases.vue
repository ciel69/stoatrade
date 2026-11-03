<script setup lang="ts">
import { computed } from 'vue'
import {
  CASES_HINT, ELIGIBLE_HINT, MODES_HINT, MemoryBlockState, SIMILAR_HINT, SIMILAR_TEXT, SMALL_HINT, SMALL_TEXT, caseActionWord, caseModeWord, caseStatusWord, caseTypeCount, dateText,
  numView, sideWord, type BlockState, type CasesDetail,
} from '@/entities/memory'
import { signed } from '@/shared/lib'
import { UiChip, UiTip } from '@/shared/ui'

/** Случаи: сколько решений помнит система, итоги закрытых сделок по режимам (режимы не смешиваются), последние случаи и заглушка поиска похожих. */
const props = defineProps<{ detail: CasesDetail | null; state: BlockState }>()

const ok = computed(() => props.state === 'ok' && !!props.detail)
const types = computed(() => Object.entries(props.detail?.by_type ?? {}).sort((a, b) => b[1] - a[1]))
const modes = computed(() => (props.detail?.modes ?? []).map((m) => ({ mode: m.mode, name: caseModeWord(m.mode), mean: numView(m.mean_r, 'r'), win: numView(m.win_share, 'pct') })))
const recent = computed(() => (props.detail?.recent ?? []).slice(0, 8))
</script>

<template>
  <section class="mc" aria-labelledby="mc-h">
    <header class="mc-head">
      <h2 id="mc-h" class="mc-title"><UiTip dotted :tip="CASES_HINT">Случаи</UiTip><span v-if="ok" class="cnt">{{ detail?.total }}</span></h2>
    </header>

    <MemoryBlockState v-if="!ok" :state="state" :reason="detail?.reason" what="о случаях" :rows="4" />
    <template v-else>
      <p class="types">
        <span v-for="[t, n] in types" :key="t">{{ caseTypeCount(t, n) }}</span>
      </p>

      <h3 class="sub"><UiTip dotted :tip="MODES_HINT">Закрытые сделки по режимам</UiTip></h3>
      <table v-if="modes.length" class="modes">
        <thead><tr><th scope="col">Режим</th><th scope="col">Средний итог</th><th scope="col">В плюс</th></tr></thead>
        <tbody>
          <tr v-for="m in modes" :key="m.mode">
            <th scope="row">
              {{ m.name }}
              <UiTip v-if="m.mean.small" :tip="SMALL_HINT" class="sm"><UiChip tone="warn">{{ SMALL_TEXT }}</UiChip></UiTip>
            </th>
            <td><b>{{ m.mean.value }}</b> <span class="n">{{ m.mean.n }}</span></td>
            <td><b>{{ m.win.value }}</b></td>
          </tr>
        </tbody>
      </table>
      <p v-else class="none">Закрытых сделок в памяти пока нет.</p>

      <h3 class="sub">Последние</h3>
      <ul v-if="recent.length" class="recent">
        <li v-for="c in recent" :key="c.case_id ?? ''" class="case">
          <span class="c-sym">{{ c.sym ?? '–' }}</span>
          <span class="c-what">{{ [sideWord(c.side), caseActionWord(c.action), caseStatusWord(c.status)].filter(Boolean).join(' · ') }}</span>
          <span class="c-r" :class="{ pos: (c.r ?? 0) > 0, neg: (c.r ?? 0) < 0 }">{{ typeof c.r === 'number' ? `${signed(c.r)} R` : '' }}</span>
          <span class="c-ts">{{ dateText(c.ts) }}</span>
          <UiTip v-if="!c.eligible" :tip="ELIGIBLE_HINT" class="c-ex"><UiChip>вне статистики</UiChip></UiTip>
        </li>
      </ul>
    </template>

    <div class="similar">
      <UiTip dotted :tip="SIMILAR_HINT" class="s-name">Поиск похожих случаев</UiTip>
      <span class="s-stage">{{ SIMILAR_TEXT }}</span>
    </div>
  </section>
</template>

<style scoped>
.mc { display: grid; gap: var(--space-3); align-content: start; padding: var(--space-4); background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); min-width: 0; }
.mc-title { margin: 0; font-size: 15px; font-weight: 700; letter-spacing: -0.01em; display: flex; align-items: baseline; gap: var(--space-2); }
.cnt { color: var(--muted); font-weight: 400; font-size: 13px; font-variant-numeric: tabular-nums; }
.sub { margin: var(--space-2) 0 0; font-size: 13px; font-weight: 600; }
.none { margin: 0; font-size: 12px; color: var(--muted); }
.types { margin: 0; display: flex; flex-wrap: wrap; gap: var(--space-1) var(--space-3); font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }

.modes { width: 100%; border-collapse: collapse; font-size: 12px; font-variant-numeric: tabular-nums; }
.modes th, .modes td { text-align: left; padding: 6px var(--space-2) 6px 0; border-top: 1px solid var(--line); vertical-align: baseline; }
.modes thead th { border-top: 0; font-weight: 400; color: var(--muted); font-size: 11px; }
.modes tbody th { font-weight: 400; }
.modes b { font-weight: 700; font-size: 13px; }
.n { font-size: 11px; color: var(--muted); white-space: nowrap; }
.sm { display: flex; margin-top: 2px; }

.recent { list-style: none; margin: 0; padding: 0; display: grid; }
.case { display: grid; grid-template-columns: 76px minmax(0, 1fr) 64px auto; align-items: baseline; gap: var(--space-1) var(--space-2); padding: 6px 0; border-top: 1px solid var(--line); font-size: 12px; }
.c-sym { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.c-what { color: var(--muted); min-width: 0; overflow-wrap: anywhere; }
.c-r { text-align: right; font-variant-numeric: tabular-nums; font-weight: 600; }
.c-r.pos { color: var(--pos); }
.c-r.neg { color: var(--neg); }
.c-ts { color: var(--muted); font-variant-numeric: tabular-nums; white-space: nowrap; }
.c-ex { grid-column: 2 / -1; }

.similar { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: var(--space-2); margin-top: var(--space-2); padding: var(--space-3); border: 1px dashed var(--line2); border-radius: var(--radius-sm); font-size: 12px; }
.s-name { color: var(--text); }
.s-stage { color: var(--muted); }

@media (max-width: 520px) {
  .case { grid-template-columns: 64px minmax(0, 1fr) auto; }
  .c-ts { grid-column: 2 / -1; }
}
</style>
