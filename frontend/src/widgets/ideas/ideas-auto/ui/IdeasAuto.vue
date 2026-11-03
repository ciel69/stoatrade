<script setup lang="ts">
import { computed, ref } from 'vue'
import { outcomeList, statCellHint, useIdeaStore, type StatCell } from '@/entities/idea'
import { ageText, fixed, signed, useNow } from '@/shared/lib'
import { UiTip } from '@/shared/ui'

/**
 * Авто-история сканера: события (резкие движения, всплески оборота), в том числе тихие, по которым мозг не торговал. Вход по цене события, издержки круга вычтены.
 * «Прод.» это ход дальше по направлению движения, «откат» против; в скобках доля плюсовых. Ячейки с n меньше 30 приглушены и помечены звёздочкой: это не вывод.
 */
const store = useIdeaStore()
const now = useNow()
const SMALL_N = 30
const limit = ref(8)

const stats = computed(() => store.snapshot?.stats ?? null)
const horizons = computed(() => stats.value?.horizons ?? [])
const rows = computed(() => stats.value?.groups ?? [])
const mine = computed(() => stats.value?.idea_groups.find((g) => g.dim === 'все') ?? null)
const recent = computed(() => store.snapshot?.auto_recent ?? [])

const tone = (v: number | undefined | null) => (v == null || v === 0 ? '' : v > 0 ? 'up' : 'down')
const low = (c: StatCell | undefined) => !!c && c.n > 0 && c.n < SMALL_N
const cell = (c: StatCell | undefined, k: 'cont' | 'fade'): string => {
  if (!c || !c.n) return '–'
  const net = k === 'cont' ? c.cont_net : c.fade_net
  const win = k === 'cont' ? c.cont_win : c.fade_win
  return `${signed(net)} (${win != null ? Math.round(win * 100) : '–'}%)${low(c) ? ' *' : ''}`
}
const mineText = computed(() => {
  const g = mine.value
  return g ? horizons.value.map((h) => { const c = g.h[String(h)]; return c?.n ? `${h}м: n=${c.n} ${signed(c.cont_gross)} → ${signed(c.cont_net)} (${Math.round((c.cont_win ?? 0) * 100)}%)` : `${h}м: n=0` }).join(' · ') : ''
})
</script>

<template>
  <section class="auto" aria-label="Авто-история">
    <template v-if="stats">
      <p class="lead">Издержки круга по taker {{ fixed(stats.costs.taker_rt_pct, 3) }}% вычтены. «Прод.» это ход дальше по направлению движения, «откат» против; в скобках доля плюсовых. Звёздочка: меньше {{ SMALL_N }} событий, это не вывод.</p>

      <div v-if="rows.length" class="scroll">
        <table class="tbl">
          <thead>
            <tr>
              <th>Группа</th><th>Значение</th><th class="n">События</th>
              <template v-for="h in horizons" :key="h"><th class="n">{{ h }}м прод.</th><th class="n">{{ h }}м откат</th></template>
            </tr>
          </thead>
          <tbody>
            <tr v-for="g in rows" :key="g.dim + g.name">
              <td class="dim">{{ g.dim }}</td><td>{{ g.name }}</td><td class="n">{{ g.events }}</td>
              <template v-for="h in horizons" :key="h">
                <td class="n" :class="low(g.h[String(h)]) ? 'dim' : tone(g.h[String(h)]?.n ? g.h[String(h)].cont_net : null)">
                  <UiTip v-if="statCellHint(g.h[String(h)], 'cont')" :tip="statCellHint(g.h[String(h)], 'cont')!">{{ cell(g.h[String(h)], 'cont') }}</UiTip>
                  <template v-else>{{ cell(g.h[String(h)], 'cont') }}</template>
                </td>
                <td class="n" :class="low(g.h[String(h)]) ? 'dim' : tone(g.h[String(h)]?.n ? g.h[String(h)].fade_net : null)">
                  <UiTip v-if="statCellHint(g.h[String(h)], 'fade')" :tip="statCellHint(g.h[String(h)], 'fade')!">{{ cell(g.h[String(h)], 'fade') }}</UiTip>
                  <template v-else>{{ cell(g.h[String(h)], 'fade') }}</template>
                </td>
              </template>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="empty"><b>Исходов ещё нет.</b> Нужен работающий сканер: первые данные приходят через 15 минут после события.</p>

      <p v-if="mineText" class="note">Идеи мозга по стороне идеи (до → после издержек, доля плюсовых): {{ mineText }}</p>

      <h3 class="sub">Последние события</h3>
      <ul v-if="recent.length" class="ev">
        <li v-for="a in recent.slice(0, limit)" :key="a.id">
          <span class="t">{{ ageText(a.ts0, now) }}</span>
          <b>{{ a.sym }}</b>
          <span>{{ a.type }}{{ a.dir === 'up' ? ' ↑' : a.dir === 'down' ? ' ↓' : '' }}</span>
          <span class="outs">
            <template v-if="outcomeList(a.h).length">
              <span v-for="o in outcomeList(a.h)" :key="o.h" :class="tone(o.net_pct)">{{ o.h }}м {{ o.dir_pct != null ? `${signed(o.dir_pct)} → ${signed(o.net_pct)}` : signed(o.ret_pct) }}</span>
            </template>
            <span v-else class="dim">ждём исходов</span>
          </span>
        </li>
      </ul>
      <p v-else class="empty">Событий пока нет.</p>
      <button v-if="recent.length > limit" type="button" class="more" @click="limit += 12">Показать ещё</button>

      <details class="how">
        <summary>Как читать эту таблицу</summary>
        <p>Сравнений в таблице: {{ stats.comparisons }}, поэтому при 5% уровне часть «плюсов» случайна. События одного часа коррелируют. Вход по цене события оптимистичен: на деле позже и хуже. В очереди на замер: {{ store.snapshot?.pending ?? 0 }}.</p>
      </details>
    </template>
    <div v-else class="skel" aria-busy="true" />
  </section>
</template>

<style scoped>
.auto { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: var(--space-4); min-width: 0; display: grid; gap: var(--space-3); }
.lead, .note, .how p { margin: 0; font-size: 12px; color: var(--muted); max-width: 80ch; line-height: 1.5; }
.note { padding: var(--space-3); border-radius: var(--radius-sm); background: var(--panel2); color: var(--text); }
.scroll { overflow-x: auto; }
.tbl { width: 100%; border-collapse: collapse; font-size: 12px; font-variant-numeric: tabular-nums; }
.tbl th { text-align: left; font-weight: 400; color: var(--muted); padding: 4px var(--space-2); white-space: nowrap; border-bottom: 1px solid var(--line); }
.tbl td { padding: 4px var(--space-2); white-space: nowrap; border-bottom: 1px solid var(--line); }
.n { text-align: right !important; }
.dim { color: var(--muted); }
.up { color: var(--pos); }
.down { color: var(--neg); }
.sub { margin: var(--space-2) 0 0; font-size: 13px; font-weight: 600; }
.ev { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(min(420px, 100%), 1fr)); gap: 6px var(--space-5); font-size: 12px; }
.ev li { display: flex; flex-wrap: wrap; align-items: baseline; gap: var(--space-3); }
.t { width: 84px; color: var(--muted); }
.outs { display: inline-flex; flex-wrap: wrap; gap: var(--space-3); font-variant-numeric: tabular-nums; }
.empty { margin: 0; font-size: 13px; color: var(--muted); }
.more { justify-self: start; padding: 6px 14px; border: 1px solid var(--line2); border-radius: var(--radius-sm); background: var(--panel2); color: var(--text); font: inherit; font-size: 12px; cursor: pointer; }
.more:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.how summary { font-size: 12px; color: var(--muted); cursor: pointer; }
.skel { height: 160px; border-radius: var(--radius-sm); background: var(--panel2); }
@media (hover: hover) and (pointer: fine) {
  .more:hover { border-color: var(--accent); }
  .how summary:hover { color: var(--text); }
}
</style>
