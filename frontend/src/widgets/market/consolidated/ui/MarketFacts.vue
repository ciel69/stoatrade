<script setup lang="ts">
import { computed } from 'vue'
import {
  CVD_WINDOWS, musd, musdSigned, pctSigned, smallPct, venueInfo, venueName,
  type CmktDetail,
} from '@/entities/cmkt'
import { UiChip, UiTip } from '@/shared/ui'

/** Факты по монете рядом со стаканом: открытый интерес, финансирование, поток ордеров, ликвидации и коды бирж. */
const props = defineProps<{ detail: CmktDetail }>()

const oi = computed(() => props.detail.oi ?? null)
const funding = computed(() => props.detail.funding ?? null)
const flow = computed(() => {
  const cvd = props.detail.cvd
  if (!cvd) return []
  return CVD_WINDOWS.flatMap(([window, label]) => {
    const v = cvd[window]?.all
    return v ? [{ key: window, label, pct: v.pct, musd: v.musd }] : []
  })
})
const liq = computed(() => props.detail.liq?.['5m'] ?? null)
const venues = computed(() => Object.entries(props.detail.venues ?? {}).map(([mkt, v]) => ({ mkt, name: venueName(mkt), info: venueInfo(v.code), lag: v.lag_ms })))
const okCount = computed(() => venues.value.filter((v) => v.info.tone === 'ok').length)
</script>

<template>
  <section class="facts" aria-label="Факты по монете">
    <div class="grid">
      <div class="tile">
        <p class="k">Открытый интерес</p>
        <p class="v">{{ oi ? musd(oi.musd) : '—' }}</p>
        <p class="s">
          <span v-if="oi?.chg_1m_pct != null">1 мин {{ pctSigned(oi.chg_1m_pct) }}</span>
          <span v-if="oi?.chg_5m_pct != null">5 мин {{ pctSigned(oi.chg_5m_pct) }}</span>
        </p>
      </div>
      <div class="tile">
        <p class="k">Финансирование</p>
        <p class="v">{{ funding ? smallPct(funding.w_pct) : '—' }}</p>
        <p class="s">
          <span>премия к споту {{ funding ? smallPct(funding.prem_ps) : '—' }}</span>
        </p>
      </div>
      <div class="tile">
        <p class="k">Поток ордеров</p>
        <p v-if="flow.length" class="v small">
          <span v-for="f in flow" :key="f.key"><b>{{ f.label }}</b> {{ pctSigned(f.pct) }} · {{ musdSigned(f.musd) }}</span>
        </p>
        <p v-else class="v">—</p>
      </div>
      <div class="tile">
        <p class="k">Ликвидации, 5 мин</p>
        <p class="v small">
          <span v-if="liq"><b class="pos">лонги</b> {{ musd(liq[0]) }}</span>
          <span v-if="liq"><b class="neg">шорты</b> {{ musd(liq[1]) }}</span>
          <span v-if="!liq">—</span>
        </p>
      </div>
    </div>

    <details v-if="venues.length" class="venues">
      <summary>Биржи по монете: {{ okCount }} из {{ venues.length }} в норме</summary>
      <ul>
        <li v-for="v in venues" :key="v.mkt">
          <span class="name">{{ v.name }}</span>
          <UiTip :tip="{ title: v.info.word, text: v.info.about }">
            <UiChip :tone="v.info.tone">{{ v.info.word }}</UiChip>
          </UiTip>
          <span class="lag">{{ v.lag == null ? '—' : `${Math.round(v.lag)} мс` }}</span>
        </li>
      </ul>
    </details>
  </section>
</template>

<style scoped>
.facts { display: grid; gap: var(--space-3); min-width: 0; align-content: start; }
.grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--space-2); }
.tile { min-width: 0; padding: var(--space-3); border: 1px solid var(--line); border-radius: var(--radius-sm); background: var(--panel2); }
.k { margin: 0 0 4px; font-size: 11px; color: var(--muted); }
.v { margin: 0; font-size: 16px; font-weight: 700; line-height: 1.2; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.v.small { display: grid; gap: 2px; font-size: 12px; font-weight: 500; }
.s { margin: 4px 0 0; display: flex; flex-wrap: wrap; gap: 2px var(--space-2); font-size: 11px; color: var(--muted); font-variant-numeric: tabular-nums; }
.pos { color: var(--pos); }
.neg { color: var(--neg); }
.venues { border-top: 1px solid var(--line); padding-top: var(--space-2); }
.venues summary { cursor: pointer; font-size: 12px; font-weight: 600; color: var(--muted); }
.venues ul { list-style: none; margin: var(--space-2) 0 0; padding: 0; display: grid; gap: 4px; }
.venues li { display: grid; grid-template-columns: minmax(0, 1fr) auto 64px; align-items: center; gap: var(--space-2); font-size: 12px; }
.name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.lag { text-align: right; font-size: 11px; color: var(--muted); font-variant-numeric: tabular-nums; }
@media (max-width: 480px) {
  .grid { grid-template-columns: minmax(0, 1fr); }
}
</style>
