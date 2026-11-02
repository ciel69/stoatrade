<script setup lang="ts">
import { computed } from 'vue'
import { useControlStore } from '@/entities/control'
import { useSystemStore } from '@/entities/system'

/**
 * Подробности по запросу: четыре строки с одной фразой-выводом, по клику справа открывается панель (её держит страница). Страница остаётся короткой: главное сверху, всё
 * технически подробное здесь. Строки: готовность к live (условия допуска), исполнитель и очередь, источники данных, история (сессии и команды).
 */
defineProps<{ selected: string | null }>()
const emit = defineEmits<{ open: [kind: string] }>()
const control = useControlStore()
const sys = useSystemStore()

const checks = computed(() => control.ctl?.checks ?? [])
const hardBad = computed(() => checks.value.filter((c) => c.sev === 'red' && !c.ok).length)
const softBad = computed(() => checks.value.filter((c) => c.sev !== 'red' && !c.ok).length)
const plural = (n: number, one: string, few: string, many: string): string => (n % 10 === 1 && n % 100 !== 11 ? one : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 12 || n % 100 > 14) ? few : many)
const rows = computed(() => {
  const s = sys.snapshot
  const out: Array<{ kind: string; title: string; line: string; tone: 'ok' | 'warn' | 'bad' | '' }> = []
  const live = control.ctl?.platform.mode === 'live'
  const n = hardBad.value
  out.push({
    // вне живой торговли невыполненное условие это справка «что нужно для перехода», а не тревога: красным оно становится, только когда торговля на реальные деньги уже идёт
    kind: 'ready', title: 'Живая торговля', tone: !checks.value.length ? '' : n && live ? 'bad' : n ? '' : softBad.value && live ? 'warn' : n === 0 ? 'ok' : '',
    line: !checks.value.length ? 'Условия появятся, когда ответит служба управления'
      : n && live ? `Не выполнено ${n} ${plural(n, 'условие', 'условия', 'условий')}: нужно ваше внимание`
        : n ? `Пока не включена. Для перехода не хватает ${n} ${plural(n, 'условия', 'условий', 'условий')} (справка)`
          : softBad.value ? 'Все обязательные условия выполнены, есть рекомендации' : 'Все условия выполнены',
  })
  if (s) {
    const unacked = Object.values(s.exec.consumers).reduce((a, c) => a + (c.unacked ?? 0), 0)
    out.push({ kind: 'exec', title: 'Исполнитель и очередь', tone: s.exec.present ? '' : 'bad', line: s.exec.present ? `Работает · позиций ${s.exec.positions ?? 0} · событий в очереди ${unacked}` : 'Исполнитель не запущен' })
    const bad = s.sources.feed.filter((f) => f.state !== 'ok').length + s.sources.degraded.length
    out.push({ kind: 'sources', title: 'Откуда берутся данные', tone: bad ? 'warn' : 'ok', line: bad ? `Не все источники отвечают: ${bad}` : 'Все источники цен и справочных данных отвечают' })
    out.push({ kind: 'history', title: 'История', tone: '', line: `Сессий: ${s.sessions.length} · последних команд: ${control.ctl?.last?.length ?? 0} · отказов за сутки: ${s.rejects.rejects ?? 0}` })
  }
  return out
})
</script>

<template>
  <section class="more" aria-label="Подробности">
    <h3 class="cap">Подробности</h3>
    <ul class="list">
      <li v-for="r in rows" :key="r.kind">
        <button type="button" class="row" :class="[`t-${r.tone}`, { sel: selected === r.kind }]" :aria-pressed="selected === r.kind" @click="emit('open', r.kind)">
          <i class="dot" aria-hidden="true" />
          <span class="t">{{ r.title }}</span>
          <span class="l">{{ r.line }}</span>
          <svg class="chev" viewBox="0 0 12 12" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4.5 3L7.5 6L4.5 9" /></svg>
        </button>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.more { display: grid; gap: var(--space-3); }
.cap { margin: 0; font-size: 12px; font-weight: 600; color: var(--muted); }
.list { list-style: none; margin: 0; padding: 0; background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); overflow: hidden; }
.list li + li { border-top: 1px solid var(--line); }
.row { display: grid; grid-template-columns: 8px 200px minmax(0, 1fr) 12px; align-items: center; gap: var(--space-3); width: 100%; padding: var(--space-3) var(--space-4); background: none; border: 0; color: inherit; font: inherit; font-size: 13px; text-align: left; cursor: pointer; transition: background-color var(--dur-fast) ease; }
.row.sel { background: var(--panel2); }
.row.sel .chev { color: var(--text); }
.row:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--line2); }
.t-ok .dot { background: var(--pos); }
.t-warn .dot { background: var(--warn); }
.t-bad .dot { background: var(--neg); }
.t { font-weight: 600; }
.l { color: var(--muted); font-size: 12px; line-height: 1.4; text-wrap: pretty; font-variant-numeric: tabular-nums; }
.chev { color: var(--muted); }
@media (max-width: 720px) {
  .row { grid-template-columns: 8px minmax(0, 1fr) 12px; }
  .l { grid-column: 2 / 3; white-space: normal; }
}
@media (hover: hover) and (pointer: fine) {
  .row:not(.sel):hover { background: var(--panel2); }
}
</style>
