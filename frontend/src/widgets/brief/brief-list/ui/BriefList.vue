<script setup lang="ts">
import { computed } from 'vue'
import { briefMs, scopeText, useBriefStore, type BriefMeta } from '@/entities/brief'

/**
 * Лента сохранённых досье: что видел мозг и когда. Строка: время, вид (старт дня, событие), точка запаса риска (зелёная: входы были разрешены, красная: запрещены), число позиций и планов,
 * пометка «обрезано» если досье не поместилось в бюджет. Выбранное подсвечено; по умолчанию страница открывает самое новое.
 */
defineProps<{ selected: string | null }>()
const emit = defineEmits<{ select: [id: string] }>()
const store = useBriefStore()

const dayFmt = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long' })
const timeFmt = new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
const key = (t: number) => { const d = new Date(t); return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}` }

interface Row { meta: BriefMeta; time: string; day: string | null }
const rows = computed<Row[]>(() => {
  const out: Row[] = []
  let last = ''
  for (const m of store.list?.items ?? []) {
    const t = briefMs(m.ts)
    const k = t == null ? last : key(t)
    out.push({ meta: m, time: t == null ? m.id : timeFmt.format(new Date(t)), day: t != null && k !== last ? (k === key(Date.now()) ? 'сегодня' : dayFmt.format(new Date(t))) : null })
    last = k
  }
  return out
})
</script>

<template>
  <nav class="list" aria-label="Сохранённые досье">
    <header class="top">
      <h2 class="title">Досье<span v-if="store.list" class="n">{{ store.list.total }}</span></h2>
    </header>
    <ul v-if="rows.length" class="items">
      <template v-for="r in rows" :key="r.meta.id">
        <li v-if="r.day" class="day">{{ r.day }}</li>
        <li>
          <button type="button" class="row" :aria-pressed="selected === r.meta.id" :title="r.meta.id" @click="emit('select', r.meta.id)">
            <i class="dot" :class="r.meta.allowed === true ? 'ok' : r.meta.allowed === false ? 'bad' : ''" :title="r.meta.allowed === true ? 'входы разрешены' : r.meta.allowed === false ? 'входы запрещены' : 'запас риска не определён'" />
            <span class="time">{{ r.time }}</span>
            <span class="scope">{{ scopeText(r.meta.scope) }}</span>
            <span class="cnt">{{ r.meta.positions }} поз · {{ r.meta.plans }} пл<template v-if="r.meta.trunc"> · <span class="cut" title="досье не поместилось в бюджет и было обрезано">обрезано</span></template></span>
          </button>
        </li>
      </template>
    </ul>
    <p v-else-if="store.list" class="empty">Сохранённых досье пока нет. Команда <code>./tb brief</code> сохраняет каждый вызов, решения ссылаются на brief_id.</p>
    <div v-else class="skel" aria-busy="true" />
  </nav>
</template>

<style scoped>
.list { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: var(--space-3); min-width: 0; display: flex; flex-direction: column; max-height: calc(100vh - var(--space-5) * 2); }
.top { padding: var(--space-1) var(--space-2) var(--space-3); }
.title { margin: 0; font-size: 14px; font-weight: 600; display: flex; align-items: baseline; gap: var(--space-2); }
.n { font-size: 12px; font-weight: 400; color: var(--muted); }
.items { list-style: none; margin: 0; padding: 0; overflow-y: auto; min-height: 0; }
.day { padding: var(--space-3) var(--space-2) var(--space-1); font-size: 11px; color: var(--muted); }
.row { display: grid; grid-template-columns: 8px auto 1fr; align-items: center; gap: 2px var(--space-2); width: 100%; padding: 7px var(--space-2); background: none; border: 0; border-radius: var(--radius-sm); color: inherit; font: inherit; font-size: 12px; text-align: left; cursor: pointer; transition: background-color var(--dur-fast) ease; }
.row[aria-pressed='true'] { background: var(--panel2); box-shadow: inset 3px 0 0 var(--accent); }
.row:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--line2); }
.dot.ok { background: var(--pos); }
.dot.bad { background: var(--neg); }
.time { font-variant-numeric: tabular-nums; font-weight: 600; }
.scope { color: var(--muted); }
.cnt { grid-column: 2 / 4; color: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }
.cut { color: var(--muted); opacity: 0.8; }
.empty { margin: 0; padding: var(--space-3); font-size: 12px; color: var(--muted); line-height: 1.5; }
.skel { height: 160px; border-radius: var(--radius-sm); background: var(--panel2); }
@media (hover: hover) and (pointer: fine) {
  .row:not([aria-pressed='true']):hover { background: var(--panel2); }
}
</style>
