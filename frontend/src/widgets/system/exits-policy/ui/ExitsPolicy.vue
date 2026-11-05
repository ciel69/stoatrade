<script setup lang="ts">
import { computed } from 'vue'
import {
  DASH, dateText, dbMessage, leaseByWord, promotionNote, promotionWord, reasonWord, switchNote, switchWord, trialVerdictWord, useExitsStore, watchCodesText, whyText, whyWord,
} from '@/entities/exit'
import { UiChip, type ChipTone } from '@/shared/ui'

/**
 * «Политика выхода» (R4, только чтение): какая версия действует, что видит исполнитель, выключатель, режим продвижения,
 * аренда, испытания, сторож и ошибки конфига. Источник — тот же отчёт, что `./tb exits status`.
 * Карточка ничего не меняет: изменения делает только владелец командами `./tb exits ...`, откат — на вкладке «Память».
 */
const store = useExitsStore()
store.start()

const p = computed(() => store.policy)
const ok = computed(() => p.value?.ok === true)
const version = computed(() => (ok.value ? p.value?.version ?? null : null))
const eff = computed(() => (ok.value ? p.value?.effective ?? null : null))
const sw = computed(() => p.value?.switch ?? null)
const promotion = computed(() => p.value?.promotion ?? null)
const trials = computed(() => p.value?.trials ?? null)
const watch = computed(() => (ok.value ? p.value?.watch ?? null : null))
const errors = computed(() => p.value?.config.errors ?? [])
const lastClosed = computed(() => (ok.value ? p.value?.trials.rows.find((r) => r.state === 'closed' && r.verdict) ?? null : null))
const dbTone = computed<ChipTone>(() => (p.value?.db === 'DB_ERROR' ? 'bad' : 'warn'))
const whyTone = computed(() => {
  const why = eff.value?.why
  if (why === 'OK') return 'ok'
  if (why === 'SWITCH' || why === 'LEASE') return 'warn'
  return 'bad'
})
const leaseLine = computed(() => {
  const lease = p.value?.lease
  if (!lease) return 'нет'
  return [lease.ver ?? DASH, leaseByWord(lease.by), dateText(lease.ts)].join(' · ')
})
const watchLine = computed(() => {
  if (!watch.value) return ''
  return `${watch.value.ver ?? DASH} · точек ${watch.value.points} · ${dateText(watch.value.ts)}`
})

function refresh(): void {
  store.request()
}
</script>

<template>
  <section class="exit" aria-label="Политика выхода" :aria-busy="store.pending || !p">
    <header class="head">
      <h3 class="cap">Политика выхода</h3>
      <button type="button" class="refresh" :disabled="store.pending" @click="refresh">обновить</button>
    </header>

    <p v-if="!p" class="loading" role="status">собираю…</p>

    <template v-else>
      <p v-if="!ok" class="db" role="status">
        <UiChip :tone="dbTone">{{ dbMessage(p.db) }}</UiChip>
        <span class="code">код {{ p.db || DASH }}</span>
      </p>

      <p class="version">
        <template v-if="version">
          <b class="mono">{{ version.ver ?? DASH }}</b>
          <span>{{ reasonWord(version.reason_code) }}</span>
          <span class="when">{{ dateText(version.ts) }}</span>
        </template>
        <span v-else-if="ok" class="muted">версии ещё нет</span>
        <span v-else class="muted">версию не видно: нет базы знаний</span>
      </p>

      <div class="eff">
        <div class="eff-line">
          <span class="k">Исполнитель видит</span>
          <span class="eff-val">
            <UiChip v-if="eff?.ver" tone="neutral">{{ eff.ver }}</UiChip>
            <UiChip v-else tone="warn">{{ DASH }}</UiChip>
            <b v-if="eff" class="why" :class="whyTone">{{ whyWord(eff.why) }}</b>
          </span>
        </div>
        <p v-if="eff" class="hint">{{ whyText(eff.why) }}</p>
        <p v-else class="hint">{{ ok ? 'сервер не вернул состояние исполнителя' : 'что видит исполнитель, без базы знаний неизвестно' }}</p>
      </div>

      <div class="rows">
        <div class="row">
          <span class="k">Выключатель</span>
          <span class="v">
            <b :class="{ off: sw?.apply === false }">{{ switchWord(sw) }}</b>
            <small>{{ switchNote(sw) }}<template v-if="sw?.ts"> · {{ dateText(sw.ts) }}</template></small>
          </span>
        </div>

        <div class="row">
          <span class="k">Продвижение</span>
          <span class="v">
            <b>{{ promotionWord(promotion?.mode) }}</b>
            <small>{{ promotionNote(promotion?.mode) }}</small>
          </span>
        </div>

        <div class="row">
          <span class="k">Аренда</span>
          <span class="v">{{ leaseLine }}</span>
        </div>

        <div class="row">
          <span class="k">Снимок</span>
          <span class="v">
            <template v-if="p.snapshot">{{ p.snapshot.ver ?? DASH }} · {{ dateText(p.snapshot.ts) }}</template>
            <template v-else>нет<small>снимка нет или он битый</small></template>
          </span>
        </div>

        <div class="row">
          <span class="k">Испытания</span>
          <span v-if="ok && trials" class="v">
            <b>открыто {{ trials.open }}, закрыто {{ trials.closed }}</b>
            <small v-if="lastClosed">последнее: {{ lastClosed.trial_id ?? DASH }} — {{ trialVerdictWord(lastClosed.verdict) }}</small>
          </span>
          <span v-else class="v">{{ DASH }}<small>нет данных без базы знаний</small></span>
        </div>

        <div class="row">
          <span class="k">Сторож</span>
          <span v-if="ok && watch" class="v">
            <b>{{ watchLine }}</b>
            <small>{{ watchCodesText(watch.codes) }}</small>
          </span>
          <span v-else-if="ok" class="v">записи нет</span>
          <span v-else class="v">{{ DASH }}<small>нет данных без базы знаний</small></span>
        </div>
      </div>

      <div v-if="errors.length" class="cfg" role="status">
        <p class="cfg-head">Конфиг: ошибки</p>
        <ul>
          <li v-for="(e, i) in errors" :key="i">{{ e }}</li>
        </ul>
      </div>

      <p class="foot">Только чтение. Изменения — <code>./tb exits ...</code>, кнопка отката — на вкладке «Память».</p>
    </template>
  </section>
</template>

<style scoped>
.exit { display: grid; gap: var(--space-3); align-content: start; min-width: 0; padding: var(--space-4); background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); container-type: inline-size; }
.head { display: flex; align-items: center; justify-content: space-between; gap: var(--space-2); }
.cap { margin: 0; font-size: 12px; font-weight: 600; color: var(--muted); }
.refresh { height: 34px; padding: 0 12px; border: 1px solid var(--line2); border-radius: var(--radius-sm); background: var(--panel2); color: var(--muted); font: inherit; font-size: 12px; font-weight: 600; cursor: pointer; transition: color var(--dur-fast) ease, border-color var(--dur-fast) ease, background-color var(--dur-fast) ease; }
.refresh:disabled { opacity: 0.55; cursor: default; }
.refresh:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.loading { margin: 0; font-size: 12px; color: var(--muted); }
.db { margin: 0; display: flex; align-items: center; gap: var(--space-2); flex-wrap: wrap; }
.db :deep(.chip) { white-space: normal; overflow-wrap: anywhere; }
.code { font-family: var(--font-mono); font-size: 11px; color: var(--muted); overflow-wrap: anywhere; }
.version { margin: 0; display: flex; align-items: baseline; gap: var(--space-2); flex-wrap: wrap; font-size: 13px; line-height: 1.45; }
.version .mono { font-family: var(--font-mono); font-size: 14px; }
.when { font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.muted { font-size: 12px; color: var(--muted); }
.eff { display: grid; gap: var(--space-1); padding: var(--space-3); background: var(--panel2); border: 1px solid var(--line); border-radius: var(--radius-sm); min-width: 0; }
.eff-line { display: flex; align-items: center; justify-content: space-between; gap: var(--space-2); flex-wrap: wrap; }
.k { font-size: 12px; color: var(--muted); }
.eff-val { display: flex; align-items: center; justify-content: flex-end; gap: var(--space-2); flex-wrap: wrap; min-width: 0; }
.why { font-size: 13px; font-weight: 600; }
.why.ok { color: var(--pos); }
.why.warn { color: var(--warn); }
.why.bad { color: var(--neg); }
.hint { margin: 0; font-size: 12px; line-height: 1.5; color: var(--muted); text-wrap: pretty; }
.rows { display: grid; gap: var(--space-3); }
/* Широкая карточка (на средней ширине она занимает всю строку) — строки в две колонки, иначе значения уезжают к правому краю. */
@container (min-width: 680px) {
  .rows { grid-template-columns: repeat(2, minmax(0, 1fr)); column-gap: var(--space-5); }
}
.row { display: grid; grid-template-columns: 92px minmax(0, 1fr); gap: 2px var(--space-3); font-size: 12px; line-height: 1.45; }
.v { min-width: 0; text-align: right; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.v b { font-weight: 600; }
.v small { display: block; color: var(--muted); font-weight: 400; text-wrap: pretty; }
.v .off { color: var(--warn); }
.cfg { display: grid; gap: var(--space-1); padding-top: var(--space-2); border-top: 1px solid var(--line); font-size: 12px; }
.cfg-head { margin: 0; color: var(--warn); font-weight: 600; }
.cfg ul { margin: 0; padding-left: var(--space-4); color: var(--muted); line-height: 1.5; overflow-wrap: anywhere; }
.foot { margin: 0; padding-top: var(--space-2); border-top: 1px solid var(--line); font-size: 11px; line-height: 1.5; color: var(--muted); text-wrap: pretty; }
.foot code { padding: 0 4px; border-radius: var(--radius-sm); background: var(--panel2); color: var(--text); font-family: var(--font-mono); font-size: 11px; overflow-wrap: anywhere; }
@media (hover: hover) and (pointer: fine) {
  .refresh:hover:not(:disabled) { border-color: var(--muted); color: var(--text); }
}
@media (prefers-reduced-motion: reduce) {
  .refresh { transition: none; }
}
</style>
