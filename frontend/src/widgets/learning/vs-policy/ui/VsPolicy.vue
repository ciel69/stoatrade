<script setup lang="ts">
import { computed } from 'vue'
import { blockView, type LearningBlock, type VsPolicyBlock } from '@/entities/learning'
import { UiChip } from '@/shared/ui'
import { policyTableView } from '../lib/policy-view'

/**
 * Мозг против теневой политики: бьёт ли мозг простые правила после издержек. Показывается только текущая версия каждой политики,
 * версии не смешиваются. Порог значимости берёт поправку Бонферрони на число политик. Виджет только показывает готовые числа сервера.
 */
const props = withDefaults(defineProps<{ block: LearningBlock; minN?: number; computingForS?: number }>(), { minN: 30, computingForS: 0 })

const view = computed(() => blockView(props.block, props.computingForS))
const t = computed(() => (view.value.kind === 'data' ? policyTableView(props.block as VsPolicyBlock, props.minN) : null))
</script>

<template>
  <section class="vp" aria-labelledby="vp-title">
    <header class="head">
      <h2 id="vp-title" class="title">{{ block.title }}</h2>
      <UiChip v-if="t" :tone="t.summaryTone">{{ t.summary }}</UiChip>
    </header>

    <p v-if="!t" class="state" :aria-busy="view.kind === 'computing'">{{ view.message }}</p>

    <template v-else>
      <p v-if="block.note" class="note">{{ block.note }}</p>

      <div v-if="t.rows.length" class="wrap" role="region" aria-label="Таблица политик" tabindex="0">
        <table>
          <thead>
            <tr>
              <th scope="col">Кто</th>
              <th scope="col" class="n" title="Сколько входов политика сделала за окно">Входов</th>
              <th scope="col" class="n" title="Средний результат в R после комиссий и проскальзывания; ниже интервал">R после издержек</th>
              <th scope="col" class="n" title="Результат мозга минус результат политики; интервал шире обычного из-за поправки">Мозг минус политика</th>
              <th scope="col">Вывод</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="t.brain" class="brain">
              <td data-label="Кто"><div class="c"><b class="nm">Мозг</b><span class="sub">свои сделки</span></div></td>
              <td class="n" data-label="Входов"><div class="c">{{ t.brain.n }}</div></td>
              <td class="n" data-label="R после издержек"><div class="c">
                <span class="v" :class="t.brain.r.tone">{{ t.brain.r.value }}</span>
                <span v-if="t.brain.r.interval" class="sub">{{ t.brain.r.interval }}</span>
                <span v-if="t.brain.r.small" class="sub warn">{{ t.brain.r.small }}</span>
              </div></td>
              <td class="n" data-label="Мозг минус политика"><div class="c"><span class="sub">–</span></div></td>
              <td data-label="Вывод"><div class="c"><span class="sub">точка отсчёта</span></div></td>
            </tr>
            <tr v-for="(r, i) in t.rows" :key="r.key" class="pol" :style="{ '--i': i }">
              <td data-label="Кто"><div class="c">
                <span class="nm-line"><b class="nm" :title="r.name">{{ r.name }}</b><UiChip v-if="r.baseline">базовая</UiChip></span>
                <span class="sub">{{ r.tag }}<template v-if="r.ctxChanged"> · контекст менялся, взят последний</template></span>
              </div></td>
              <td class="n" data-label="Входов"><div class="c">
                {{ r.filled }}
                <span v-if="r.fill" class="sub">{{ r.fill }}</span>
              </div></td>
              <td class="n" data-label="R после издержек"><div class="c">
                <span class="v" :class="r.r.tone">{{ r.r.value }}</span>
                <span v-if="r.r.interval" class="sub">{{ r.r.interval }}</span>
                <span v-if="r.r.small" class="sub warn">{{ r.r.small }}</span>
              </div></td>
              <td class="n" data-label="Мозг минус политика"><div class="c">
                <span class="v" :class="r.diff.tone">{{ r.diff.value }}</span>
                <span v-if="r.diff.interval" class="sub">{{ r.diff.interval }}</span>
              </div></td>
              <td data-label="Вывод"><div class="c">
                <UiChip :tone="r.tone">{{ r.verdict }}</UiChip>
                <span class="sub">{{ r.p }}</span>
              </div></td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="state">Включённых политик нет.</p>

      <p v-if="t.correction" class="foot">{{ t.correction }}<template v-if="t.level"> Интервалы разности уровня {{ t.level }}.</template> Показана текущая версия каждой политики, версии не смешиваются.</p>
      <ul v-if="t.caveats.length" class="cav" aria-label="Оговорки сравнения">
        <li v-for="c in t.caveats" :key="c">{{ c }}</li>
      </ul>
    </template>
  </section>
</template>

<style scoped>
.vp { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: var(--space-4); min-width: 0; }
.head { display: flex; align-items: center; gap: var(--space-3); margin-bottom: var(--space-3); flex-wrap: wrap; }
.title { margin: 0; font-size: 14px; font-weight: 600; }
.state { margin: 0; color: var(--muted); font-size: 13px; }
.note { margin: 0 0 var(--space-3); color: var(--muted); font-size: 12px; line-height: 1.4; }
.wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 12px; font-variant-numeric: tabular-nums; }
th, td { padding: var(--space-2); border-top: 1px solid var(--line); text-align: left; vertical-align: top; font-weight: 400; }
th { color: var(--muted); border-top: 0; white-space: nowrap; cursor: help; }
.n { text-align: right; }
td.n { white-space: nowrap; }
.c > .sub, .c > .v { display: block; }
.nm-line { display: flex; align-items: center; gap: var(--space-2); min-width: 0; }
.nm { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 22ch; }
.v { font-weight: 600; }
.sub { color: var(--muted); font-size: 11px; line-height: 1.4; }
.sub.warn { color: var(--warn); }
.pos { color: var(--pos); }
.neg { color: var(--neg); }
.brain td { background: var(--panel2); }
.foot { margin: var(--space-3) 0 0; font-size: 11px; color: var(--muted); line-height: 1.4; }
.cav { margin: var(--space-2) 0 0; padding-left: var(--space-4); font-size: 11px; color: var(--muted); line-height: 1.5; }

/* появление строк: прозрачность и сдвиг, 30 мс между строками; один раз при показе (данные приходят по запросу, не потоком) */
.pol { animation: vp-in var(--dur-enter) var(--ease-out) both; animation-delay: calc(var(--i, 0) * 30ms); }
@keyframes vp-in {
  from { opacity: 0; transform: translateY(4px); }
}

@media (hover: hover) {
  .pol { transition: background-color var(--dur-fast) ease; }
  .pol:hover { background: var(--panel2); }
}

@media (prefers-reduced-motion: reduce) {
  .pol { animation-name: vp-fade; animation-delay: 0s; }
  @keyframes vp-fade { from { opacity: 0; } }
}

/* узкий экран: строка таблицы становится блоком с подписями полей */
@media (max-width: 640px) {
  thead { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }
  table, tbody, tr, td { display: block; }
  tr { border-top: 1px solid var(--line); padding: var(--space-2) 0; }
  tr:first-child { border-top: 0; }
  td { border-top: 0; padding: 2px var(--space-2); display: grid; grid-template-columns: 9.5em minmax(0, 1fr); gap: var(--space-2); align-items: baseline; }
  td::before { content: attr(data-label); color: var(--muted); font-size: 11px; }
  td.n { text-align: left; }
  .brain td { background: none; }
  .brain { background: var(--panel2); }
  .nm { max-width: none; }
}
</style>
