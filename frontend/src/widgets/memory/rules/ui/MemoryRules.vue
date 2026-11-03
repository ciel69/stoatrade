<script setup lang="ts">
import { computed } from 'vue'
import {
  FEAT_HINT, MemoryBlockState, NO_EVENT_HINT, RULE_KIND_TEXT, SMALL_HINT, SMALL_TEXT, VERSION_HINT, applyView, dateText, noEventText, numView, ruleCountText, ruleHint, ruleText, versionReasonWord,
  waitHint, waitTermText, waitWord, type BlockState, type RuleBrief, type RulesDetail,
} from '@/entities/memory'
import { UiChip, UiTip } from '@/shared/ui'

/**
 * Действующий набор правил: применяются ли правила сейчас и почему, версия, действующие правила (с аварийной кнопкой «Отключить»),
 * ожидающие включения с причиной и сроком, доля проверок без данных и решений без события, история версий.
 */
const props = defineProps<{ detail: RulesDetail | null; state: BlockState; busy: boolean }>()
const emit = defineEmits<{ disable: [rule: RuleBrief] }>()

const ok = computed(() => props.state === 'ok' && !!props.detail)
const mode = computed(() => (props.detail?.mode ? applyView(props.detail.mode) : null))
const active = computed(() => props.detail?.active ?? [])
const waiting = computed(() => props.detail?.waiting ?? [])
const versions = computed(() => (props.detail?.versions ?? []).slice(0, 8))
const feat = computed(() => (props.detail?.feat_missing ? numView(props.detail.feat_missing.all, 'pct') : null))
const featByRule = computed(() => (props.detail?.feat_missing?.by_rule ?? []).filter((r) => r.n > 0).slice(0, 6))
const noEv = computed(() => (props.detail?.no_event ? numView(props.detail.no_event, 'pct') : null))
const days = computed(() => props.detail?.no_event?.window_days ?? props.detail?.feat_missing?.window_days ?? 7)
</script>

<template>
  <section class="mr" aria-labelledby="mr-h">
    <header class="mr-head">
      <h2 id="mr-h" class="mr-title">Правила сейчас</h2>
      <UiTip v-if="detail?.current" :tip="VERSION_HINT" class="ver">версия&nbsp;<b>{{ detail.current }}</b></UiTip>
    </header>

    <div v-if="mode" class="apply" :class="mode.tone" role="status">
      <span class="a-dot" aria-hidden="true" />
      <div>
        <p class="a-title">{{ mode.title }}</p>
        <p class="a-text">{{ mode.text }}</p>
      </div>
    </div>

    <MemoryBlockState v-if="!ok" :state="state" :reason="detail?.reason" what="о правилах" :rows="4" />
    <template v-else>
      <p class="kind">{{ RULE_KIND_TEXT }}</p>

      <h3 class="sub">Действуют <span class="cnt">{{ active.length }}</span></h3>
      <ul v-if="active.length" class="list">
        <li v-for="r in active" :key="r.rule_id ?? ''" class="row">
          <div class="r-main">
            <p class="r-text">{{ ruleText(r) }}</p>
            <p class="r-meta">
              <UiTip dotted :tip="ruleHint(r)">{{ r.rule_id }}</UiTip>
              <span v-if="r.ver_in">с версии {{ r.ver_in }}</span>
            </p>
          </div>
          <button type="button" class="off" :disabled="busy" :aria-label="`Отключить правило ${r.rule_id}`" @click="emit('disable', r)">Отключить</button>
        </li>
      </ul>
      <p v-else class="none">Действующих правил нет: мозг торгует без ограничений от правил.</p>

      <h3 class="sub">Ждут включения <span class="cnt">{{ waiting.length }}</span></h3>
      <ul v-if="waiting.length" class="list">
        <li v-for="w in waiting" :key="w.rule_id ?? ''" class="row">
          <div class="r-main">
            <p class="r-text">{{ ruleText(w) }}</p>
            <p class="r-meta">
              <UiTip :tip="waitHint(w)"><UiChip :tone="w.wait === 'LOSS_CAP' ? 'warn' : 'accent'">{{ waitWord(w.wait) }}</UiChip></UiTip>
              <span>{{ waitTermText(w) }}</span>
              <span class="mono">{{ w.rule_id }}</span>
            </p>
          </div>
        </li>
      </ul>
      <p v-else class="none">Ожидающих правил нет.</p>

      <h3 class="sub">Качество проверок за {{ days }} дн.</h3>
      <dl class="stats">
        <div v-if="feat" class="stat">
          <dt><UiTip dotted :tip="FEAT_HINT">Нет данных для проверки</UiTip></dt>
          <dd>
            <b class="val">{{ feat.value }}</b>
            <span class="n">{{ feat.n }}</span>
            <UiTip v-if="feat.small" :tip="SMALL_HINT"><UiChip>{{ SMALL_TEXT }}</UiChip></UiTip>
            <ul v-if="featByRule.length" class="by" aria-label="По правилам">
              <li v-for="r in featByRule" :key="r.rule_id ?? ''"><span class="mono">{{ r.rule_id }}</span> {{ numView(r, 'pct').value }} <span class="n">n = {{ r.n }}</span></li>
            </ul>
          </dd>
        </div>
        <div v-if="noEv" class="stat">
          <dt><UiTip dotted :tip="NO_EVENT_HINT">Решения без события</UiTip></dt>
          <dd>
            <b class="val" :class="{ warn: detail?.no_event?.yellow }">{{ noEv.value }}</b>
            <span class="n">{{ noEv.n }}</span>
            <UiTip v-if="noEv.small" :tip="SMALL_HINT"><UiChip>{{ SMALL_TEXT }}</UiChip></UiTip>
            <span class="note" :class="{ warn: detail?.no_event?.yellow }">{{ noEventText(!!detail?.no_event?.yellow, days) }}</span>
          </dd>
        </div>
      </dl>

      <details v-if="versions.length" class="hist">
        <summary>История версий <span class="cnt">{{ detail?.versions_total ?? versions.length }}</span></summary>
        <ol class="vers">
          <li v-for="v in versions" :key="v.ver ?? ''">
            <span class="mono v-id">{{ v.ver }}</span>
            <span>{{ versionReasonWord(v.reason_code) }}</span>
            <span class="v-meta">{{ ruleCountText(v.rule_ids.length) }} · {{ dateText(v.ts) }}</span>
          </li>
        </ol>
      </details>
    </template>
  </section>
</template>

<style scoped>
.mr { display: grid; gap: var(--space-3); align-content: start; padding: var(--space-4); background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); min-width: 0; }
.mr-head { display: flex; align-items: baseline; justify-content: space-between; gap: var(--space-3); }
.mr-title { margin: 0; font-size: 15px; font-weight: 700; letter-spacing: -0.01em; }
.ver { font-size: 12px; color: var(--muted); }
.ver b { font-family: var(--font-mono); font-weight: 600; color: var(--text); }
.mono { font-family: var(--font-mono); font-size: 11px; color: var(--muted); }
.cnt { margin-left: var(--space-1); color: var(--muted); font-weight: 400; font-variant-numeric: tabular-nums; }

.apply { display: grid; grid-template-columns: 10px minmax(0, 1fr); gap: var(--space-3); align-items: start; padding: var(--space-3); border-radius: var(--radius-sm); background: var(--panel2); }
.a-dot { width: 10px; height: 10px; margin-top: 4px; border-radius: 50%; background: var(--muted); }
.apply.ok .a-dot { background: var(--pos); }
.apply.warn .a-dot { background: var(--warn); }
.a-title { margin: 0; font-size: 13px; font-weight: 600; }
.a-text { margin: 2px 0 0; font-size: 12px; line-height: 1.5; color: var(--muted); max-width: 70ch; }

.kind { margin: 0; font-size: 12px; line-height: 1.5; color: var(--muted); max-width: 72ch; }
.sub { margin: var(--space-2) 0 0; font-size: 13px; font-weight: 600; }
.none { margin: 0; font-size: 12px; color: var(--muted); }

.list { list-style: none; margin: 0; padding: 0; display: grid; }
.row { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); padding: var(--space-2) 0; border-top: 1px solid var(--line); min-width: 0; }
.r-main { min-width: 0; display: grid; gap: 2px; }
.r-text { margin: 0; font-size: 13px; line-height: 1.45; overflow-wrap: anywhere; }
.r-meta { margin: 0; display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-1) var(--space-3); font-size: 11px; color: var(--muted); }

.off { flex: none; min-height: 32px; padding: 0 var(--space-3); border-radius: var(--radius-sm); border: 1px solid var(--line2); background: transparent; color: var(--neg); font-size: 12px; font-weight: 600; cursor: pointer; transition: border-color var(--dur-fast) ease, background-color var(--dur-fast) ease, transform 120ms var(--ease-out); }
.off:disabled { opacity: 0.5; cursor: not-allowed; }
.off:active:not(:disabled) { transform: scale(0.97); }
@media (hover: hover) and (pointer: fine) {
  .off:not(:disabled):hover { border-color: var(--neg); }
}

.stats { margin: 0; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--space-3); }
.stat { display: grid; gap: var(--space-1); align-content: start; padding: var(--space-3); border-radius: var(--radius-sm); background: var(--panel2); min-width: 0; }
.stat dt { font-size: 12px; color: var(--muted); }
.stat dd { margin: 0; display: flex; flex-wrap: wrap; align-items: baseline; gap: var(--space-2); }
.val { font-size: 20px; font-weight: 700; letter-spacing: -0.02em; font-variant-numeric: tabular-nums; }
.val.warn, .note.warn { color: var(--warn); }
.n { font-size: 11px; color: var(--muted); font-variant-numeric: tabular-nums; }
.note { flex-basis: 100%; margin: 0; font-size: 11px; line-height: 1.45; color: var(--muted); }
.by { flex-basis: 100%; list-style: none; margin: var(--space-1) 0 0; padding: 0; display: grid; gap: 2px; font-size: 12px; font-variant-numeric: tabular-nums; }

.hist summary { cursor: pointer; min-height: 32px; display: flex; align-items: center; font-size: 13px; font-weight: 600; border-radius: var(--radius-sm); }
.vers { list-style: none; margin: var(--space-1) 0 0; padding: 0; display: grid; }
.vers li { display: grid; grid-template-columns: 64px minmax(0, 1fr) auto; gap: var(--space-3); align-items: baseline; padding: 6px 0; border-top: 1px solid var(--line); font-size: 12px; }
.v-id { color: var(--text); }
.v-meta { color: var(--muted); font-variant-numeric: tabular-nums; white-space: nowrap; }

@media (max-width: 520px) {
  .stats { grid-template-columns: minmax(0, 1fr); }
  .vers li { grid-template-columns: 56px minmax(0, 1fr); }
  .v-meta { grid-column: 2; white-space: normal; }
}
</style>
