<script setup lang="ts">
import { computed } from 'vue'
import { levelAxis, type BriefLevel } from '@/entities/brief'
import { fixed, price } from '@/shared/lib'

/** Уровни одной монеты относительно цены: полосы недельного, дневного и вчерашнего диапазонов, свинги тонкими линиями, цена белой чертой с подписью. */
const props = defineProps<{ sym: string; level: BriefLevel }>()
const axis = computed(() => levelAxis(props.level))
const pct = (x: number) => `${(x * 100).toFixed(2)}%`
</script>

<template>
  <div class="lv">
    <div class="head">
      <router-link class="sym" :to="{ path: '/market', query: { coin: sym } }">{{ sym }}</router-link>
      <span class="meta">ATR 5м {{ fixed(level.atr_5m, 3) }} · медиана объёма {{ level.vol_med_5m != null ? Math.round(level.vol_med_5m) : '–' }}</span>
    </div>
    <div v-if="axis" class="chart" role="img" :aria-label="`${sym}: цена ${price(axis.px)} относительно диапазонов недели, дня и вчера`">
      <div v-for="b in axis.bands" :key="b.key" class="band-row">
        <span class="lab">{{ b.label }}</span>
        <span class="track">
          <i class="band" :class="`b-${b.key}`" :style="{ left: pct(axis.at(b.lo)), width: pct(axis.at(b.hi) - axis.at(b.lo)) }" :title="`${b.label}: ${price(b.lo)} – ${price(b.hi)}`" />
        </span>
        <span class="rng">{{ price(b.lo) }} – {{ price(b.hi) }}</span>
      </div>
      <span class="over" aria-hidden="true">
        <i v-for="(s, i) in axis.swings" :key="i" class="swing" :style="{ left: pct(axis.at(s)) }" />
        <i v-if="axis.px != null" class="px" :style="{ left: pct(axis.at(axis.px)) }"><b>{{ price(axis.px) }}</b></i>
      </span>
    </div>
    <p v-else class="muted">Недостаточно уровней для диаграммы.</p>
    <p v-if="level.swing_5m?.length" class="muted">свинги 5м: {{ level.swing_5m.map(price).join(', ') }}</p>
  </div>
</template>

<style scoped>
.lv { display: grid; gap: var(--space-2); }
.head { display: flex; align-items: baseline; gap: var(--space-3); flex-wrap: wrap; }
.sym { font-weight: 700; color: var(--text); text-decoration: none; }
.sym:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.meta, .muted { margin: 0; font-size: 11px; color: var(--muted); }
.chart { position: relative; display: grid; gap: 8px; padding: var(--space-4) 0 var(--space-3); }
.band-row { display: grid; grid-template-columns: 52px minmax(0, 1fr) 128px; align-items: center; gap: var(--space-2); font-size: 11px; }
.lab { color: var(--muted); }
.track { position: relative; height: 8px; border-radius: 4px; background: var(--panel2); }
.band { position: absolute; top: 0; bottom: 0; border-radius: 4px; background: var(--line2); }
.band.b-week { background: color-mix(in srgb, var(--accent) 30%, var(--line2)); }
.band.b-day { background: color-mix(in srgb, var(--accent) 55%, var(--line2)); }
.rng { color: var(--muted); font-variant-numeric: tabular-nums; text-align: right; white-space: nowrap; }
.over { position: absolute; top: 0; bottom: 0; left: 52px; right: 128px; margin: 0 var(--space-2); pointer-events: none; }
.swing { position: absolute; top: 14px; bottom: 6px; width: 1px; background: var(--muted); opacity: 0.6; }
.px { position: absolute; top: 6px; bottom: 0; width: 2px; margin-left: -1px; background: var(--text); }
.px b { position: absolute; top: -16px; left: 50%; transform: translateX(-50%); font-size: 11px; font-weight: 600; font-variant-numeric: tabular-nums; white-space: nowrap; }
@media (hover: hover) and (pointer: fine) {
  .sym:hover { color: var(--accent); }
}
</style>
