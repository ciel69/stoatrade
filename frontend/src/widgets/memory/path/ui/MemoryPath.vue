<script setup lang="ts">
import { computed } from 'vue'
import { FUNNEL_HINTS, MemoryBlockState, PATH_INTRO, VERSION_HINT, pathLabel, pathSteps, ruleText, type BlockState, type PathDetail } from '@/entities/memory'
import { UiTip } from '@/shared/ui'

/**
 * Путь «кандидат → урок → испытание → правило → версия»: сверху воронка (сколько дошло до каждого шага), ниже путь каждого недавнего кандидата.
 * Единственный «авторский» момент движения вкладки: при появлении данных линия воронки прорисовывается слева направо, станции проявляются лесенкой.
 */
const props = defineProps<{ detail: PathDetail | null; state: BlockState; now: number; version: string | null }>()

const KEYS = ['candidates', 'hyp', 'trial', 'confirmed', 'rule_active'] as const
const LABEL: Record<(typeof KEYS)[number], string> = { candidates: 'Кандидаты', hyp: 'Уроки', trial: 'Испытания', confirmed: 'Подтверждены', rule_active: 'Правила действуют' }

const funnel = computed(() => {
  const f = props.detail?.funnel
  return KEYS.map((k) => ({ key: k, label: LABEL[k], value: f && Number.isFinite(f[k]) ? f[k] : null, tip: FUNNEL_HINTS[k] }))
})
const ok = computed(() => props.state === 'ok' && !!props.detail)
const rows = computed(() => (ok.value ? (props.detail!.paths ?? []).slice(0, 12).map((p, i) => {
  const steps = pathSteps(p, props.now)
  return { key: `${p.candidate.id ?? i}`, title: p.lesson?.txt || (p.rule?.cond_txt ? ruleText(p.rule) : '') || `Кандидат ${p.candidate.id ?? ''}`.trim(), steps, label: pathLabel(steps) }
}) : []))
const shown = computed(() => (props.detail?.paths?.length ?? 0))
</script>

<template>
  <section class="mp" aria-labelledby="mp-h">
    <header class="mp-head">
      <h2 id="mp-h" class="mp-title">Как наблюдение становится правилом</h2>
      <p class="mp-intro">{{ PATH_INTRO }}</p>
    </header>

    <ol class="funnel" :class="{ on: ok }" aria-label="Воронка: сколько дошло до каждого шага">
      <li v-for="(f, i) in funnel" :key="f.key" class="st" :style="{ '--i': i }">
        <span class="dot" aria-hidden="true" />
        <b class="num">{{ ok && f.value !== null ? f.value : '–' }}</b>
        <UiTip dotted :tip="f.tip" class="lbl">{{ f.label }}</UiTip>
      </li>
      <li class="st ver" :style="{ '--i': 5 }">
        <span class="dot" aria-hidden="true" />
        <b class="num mono">{{ version ?? '–' }}</b>
        <UiTip dotted :tip="VERSION_HINT" class="lbl">Версия правил</UiTip>
      </li>
    </ol>

    <MemoryBlockState v-if="!ok" :state="state" :reason="detail?.reason" what="о пути уроков" :rows="2" />
    <template v-else-if="rows.length">
      <h3 class="mp-sub">Недавние кандидаты <span class="cnt">{{ shown }}</span></h3>
      <ul class="paths">
        <li v-for="r in rows" :key="r.key" class="path">
          <p class="p-title">{{ r.title }}</p>
          <ol class="steps" :aria-label="r.label">
            <li v-for="s in r.steps" :key="s.name" class="step" :class="s.state">
              <span class="s-mark" aria-hidden="true" />
              <span class="s-name">{{ s.name }}</span>
              <span v-if="s.word" class="s-word">{{ s.word }}</span>
            </li>
          </ol>
        </li>
      </ul>
    </template>
    <p v-else class="empty">Кандидатов пока нет: недельная проверка ещё не находила условий, где сделки заметно хуже обычного.</p>
  </section>
</template>

<style scoped>
.mp { display: grid; gap: var(--space-4); padding: var(--space-5); background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); min-width: 0; }
.mp-head { display: grid; gap: var(--space-1); }
.mp-title { margin: 0; font-size: 15px; font-weight: 700; letter-spacing: -0.01em; }
.mp-intro { margin: 0; font-size: 13px; line-height: 1.5; color: var(--muted); max-width: 72ch; text-wrap: pretty; }
.mp-sub { margin: var(--space-2) 0 0; font-size: 13px; font-weight: 600; }
.cnt { margin-left: var(--space-1); color: var(--muted); font-weight: 400; font-variant-numeric: tabular-nums; }
.empty { margin: 0; font-size: 13px; color: var(--muted); }

/* воронка: станции на одной линии; линия — псевдоэлемент на всю ширину, прорисовывается через scaleX */
.funnel { position: relative; list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: var(--space-3); }
.funnel::before { content: ""; position: absolute; left: 6px; right: 6px; top: 5px; height: 2px; border-radius: 2px; background: var(--line2); transform-origin: left center; transform: scaleX(0); transition: transform 420ms var(--ease-out); }
.funnel.on::before { transform: scaleX(1); }
.st { position: relative; display: grid; justify-items: start; gap: 2px; min-width: 0; opacity: 0.55; transition: opacity var(--dur-enter) var(--ease-out), transform var(--dur-enter) var(--ease-out); transition-delay: calc(var(--i) * 50ms + 80ms); }
.funnel.on .st { opacity: 1; }
.funnel:not(.on) .st { transform: translateY(2px); }
.dot { width: 12px; height: 12px; border-radius: 50%; background: var(--panel); border: 2px solid var(--line2); margin-bottom: var(--space-2); }
.funnel.on .st .dot { border-color: var(--accent); }
.funnel.on .st.ver .dot { border-color: var(--pos); background: var(--pos); }
.num { font-size: 26px; font-weight: 700; line-height: 1.1; letter-spacing: -0.02em; font-variant-numeric: tabular-nums; }
.num.mono { font-size: 18px; font-family: var(--font-mono); letter-spacing: 0; line-height: 1.45; }
.lbl { font-size: 12px; color: var(--muted); }

/* путь кандидата: пять шагов в строку */
.paths { list-style: none; margin: 0; padding: 0; display: grid; }
.path { display: grid; gap: var(--space-2); padding: var(--space-3) 0; border-top: 1px solid var(--line); min-width: 0; }
.p-title { margin: 0; font-size: 13px; line-height: 1.45; overflow-wrap: anywhere; }
.steps { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: var(--space-2); }
.step { display: grid; grid-template-columns: 8px minmax(0, 1fr); column-gap: var(--space-2); align-items: center; font-size: 12px; min-width: 0; }
.s-mark { width: 8px; height: 8px; border-radius: 50%; border: 1.5px solid var(--line2); }
.s-name { color: var(--muted); }
.s-word { grid-column: 2; color: var(--text); overflow-wrap: anywhere; line-height: 1.35; }
.step.done .s-mark { background: var(--pos); border-color: var(--pos); }
.step.now .s-mark { border-color: var(--accent); background: var(--accent-bg); }
.step.fail .s-mark { border-color: var(--neg); }
.step.fail .s-word { color: var(--neg); }
.step.none .s-mark { border-style: dashed; }

@media (max-width: 900px) {
  .funnel { grid-template-columns: repeat(3, minmax(0, 1fr)); row-gap: var(--space-4); }
  .funnel::before { display: none; }
  .steps { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 520px) {
  .mp { padding: var(--space-4); }
  .funnel { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .steps { grid-template-columns: minmax(0, 1fr); }
}
@media (prefers-reduced-motion: reduce) {
  .funnel::before { transition: none; }
  .st, .funnel:not(.on) .st { transition: none; transform: none; }
}
</style>
