<script setup lang="ts">
import { computed } from 'vue'
import { dayRows, useDayStore } from '@/entities/day'

/**
 * Список торговых дней, новые сверху: дата и номер запуска, состояние, режим, итог и оценка. У дня с готовым отчётом строка кнопка: отчёт открывается в боковой панели (выбор хранится в адресе, ?day=),
 * повторный клик закрывает. Отметка «не прочитан» у дня, итог которого платформа ещё ждёт отметить.
 */
defineProps<{ selected: string | null }>()
const emit = defineEmits<{ select: [id: string] }>()
const store = useDayStore()
const snap = computed(() => store.snapshot)
const rows = computed(() => dayRows(snap.value?.days ?? [], store.needs.report))
</script>

<template>
  <section class="list" aria-labelledby="dl-h">
    <h2 id="dl-h" class="h">Дни<span v-if="rows.length" class="n">{{ rows.length }}</span></h2>
    <ul v-if="rows.length" class="rows">
      <li v-for="r in rows" :key="r.id">
        <component :is="r.hasReport ? 'button' : 'div'" class="row" :class="{ btn: r.hasReport }" :type="r.hasReport ? 'button' : undefined" :aria-pressed="r.hasReport ? selected === r.id : undefined" @click="r.hasReport && emit('select', r.id)">
          <span class="top">
            <b class="lbl">{{ r.label }}</b>
            <span v-if="r.unread" class="unread">не прочитан</span>
            <span v-if="r.net" class="net" :class="r.netTone ?? ''">{{ r.net }}</span>
          </span>
          <span class="meta">
            <span>{{ r.state }}, {{ r.mode }}</span>
            <span v-if="r.verdict" class="vd" :class="r.verdictTone ?? ''">{{ r.verdict }}</span>
            <span v-else-if="!r.hasReport" class="none">отчёта нет</span>
          </span>
        </component>
      </li>
    </ul>
    <p v-else-if="snap" class="empty">Дней ещё не было. Запуск: «Старт торговли».</p>
    <div v-else class="skel" aria-busy="true" />
  </section>
</template>

<style scoped>
.list { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: var(--space-3); min-width: 0; align-content: start; display: grid; gap: var(--space-2); }
.h { margin: 0; padding: var(--space-2) var(--space-2) 0; font-size: 14px; font-weight: 600; display: flex; align-items: baseline; gap: var(--space-2); }
.n { font-size: 12px; font-weight: 400; color: var(--muted); }
.rows { list-style: none; margin: 0; padding: 0; display: grid; }
.row { display: grid; gap: 2px; width: 100%; padding: var(--space-2); background: none; border: 0; border-radius: var(--radius-sm); color: inherit; font: inherit; text-align: left; }
.row.btn { cursor: pointer; transition: background-color var(--dur-fast) ease; }
.row[aria-pressed='true'] { background: var(--panel2); box-shadow: inset 0 0 0 1px var(--line2); }
.row.btn:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.top { display: flex; align-items: baseline; flex-wrap: wrap; gap: var(--space-1) var(--space-3); font-size: 13px; }
.lbl { font-weight: 600; }
.unread { font-size: 11px; color: var(--accent); border: 1px solid var(--accent); border-radius: 99px; padding: 0 7px; line-height: 1.6; }
.net { margin-left: auto; font-variant-numeric: tabular-nums; font-weight: 600; }
.net.pos { color: var(--pos); }
.net.neg { color: var(--neg); }
.meta { display: flex; flex-wrap: wrap; gap: var(--space-1) var(--space-3); font-size: 12px; color: var(--muted); }
.vd.ok { color: var(--pos); }
.vd.warn { color: var(--warn); }
.vd.bad { color: var(--neg); }
.none { opacity: 0.8; }
.empty { margin: 0; padding: var(--space-2); font-size: 13px; line-height: 1.5; color: var(--muted); }
.skel { height: 140px; border-radius: var(--radius-sm); background: var(--panel2); }
@media (hover: hover) and (pointer: fine) {
  .row.btn:not([aria-pressed='true']):hover { background: var(--panel2); }
}
@media (prefers-reduced-motion: reduce) {
  .row.btn { transition: none; }
}
</style>
