<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { collapseEvents, eventFields, PRIORITY_TEXT, useEventStore } from '@/entities/event'
import { UiDetail, UiSidePanel } from '@/shared/ui'

const events = useEventStore()
const open = ref(new URLSearchParams(location.search).has('events'))        // ?events открывает панель сразу (для скриншотов и ссылок)
const panel = ref<HTMLElement | null>(null)
const button = ref<HTMLButtonElement | null>(null)

onMounted(() => events.start())

const badge = computed(() => (events.unread > 99 ? '99+' : String(events.unread)))
const timeFmt = new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
const dayFmt = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long' })
const dayKey = (d: Date) => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`
const today = () => dayKey(new Date())

/** Строки списка: события шины приходят в UTC, показываем местное время; подряд идущие одинаковые свёрнуты; при смене суток вставляем разделитель. */
const rows = computed(() => {
  const out: Array<{ key: string; day?: string; g?: ReturnType<typeof collapseEvents>[number]; time?: string; i: number }> = []
  let last = ''
  let i = 0
  for (const g of collapseEvents(events.items)) {
    const d = new Date(g.e.ts)
    const valid = !Number.isNaN(d.getTime())
    const k = valid ? dayKey(d) : last
    if (valid && k !== last) {
      out.push({ key: `day-${k}`, day: k === today() ? 'сегодня' : dayFmt.format(d), i: 0 })
      last = k
    }
    out.push({ key: String(g.e.seq), g, time: valid ? timeFmt.format(d) : g.e.ts.slice(11, 19), i: Math.min(i++, 8) })
  }
  return out
})

/** Выбранная строка (по номеру события): детали одного события или группы повторов открываются боковой панелью поверх страницы. */
const sel = ref<string | null>(null)
function flip(key: string): void {
  sel.value = sel.value === key ? null : key
}
const selRow = computed(() => rows.value.find((r) => r.key === sel.value && r.g)?.g ?? null)
const fullFmt = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
const at = (e: { ts: string }) => {
  const d = new Date(e.ts)
  return Number.isNaN(d.getTime()) ? e.ts : fullFmt.format(d)
}
/** Все известные поля события в виде пар «подпись, значение». */
function moreOf(g: ReturnType<typeof collapseEvents>[number]): Array<[string, string]> {
  const e = g.e
  const out: Array<[string, string]> = [['время', at(e)]]
  if (g.count > 1) out.push(['повторы', `${g.count} раз подряд: с ${timeFmt.format(new Date(g.oldest.ts))} до ${timeFmt.format(new Date(e.ts))}`])
  out.push(['тип', e.type], ['важность', PRIORITY_TEXT[e.pri] ?? e.pri])
  if (e.sym) out.push(['монета', e.sym])
  if (e.plan) out.push(['план', e.plan])
  out.push(...eventFields(e), ['номер', String(e.seq)])
  return out
}

if (new URLSearchParams(location.search).get('events') === 'expand') {        // ?events=expand: сразу открыть панель первой строки (для скриншотов)
  const stop = watch(rows, (r) => {
    const first = r.find((x) => x.g)
    if (first) { sel.value = first.key; stop() }
  })
}

function toggle(): void {
  open.value = !open.value
}
function close(): void {
  open.value = false
  button.value?.focus()
}
/** Esc сначала закрывает боковую панель события (её закрывает сама панель), и только следующий закрывает список: слушаем в фазе перехвата, пока выбор ещё не сброшен. */
function onKey(ev: KeyboardEvent): void {
  if (ev.key === 'Escape' && open.value && !sel.value) close()
}
onMounted(() => window.addEventListener('keydown', onKey, true))
onUnmounted(() => window.removeEventListener('keydown', onKey, true))

watch(open, async (v) => {
  if (v) {
    events.markSeen()
    await nextTick()
    panel.value?.focus()
  }
})
// пока панель открыта, всё приходящее сразу считается прочитанным
watch(() => events.lastSeq, () => open.value && events.markSeen())
</script>

<template>
  <div class="cloud">
    <Transition name="panel">
      <aside v-if="open" id="event-panel" ref="panel" class="panel" tabindex="-1" aria-label="События">
        <header class="head">
          <span>События</span>
          <button type="button" class="x" aria-label="Закрыть события" @click="close">×</button>
        </header>
        <TransitionGroup v-if="rows.length" name="feed" tag="ul" class="list">
          <li v-for="r in rows" :key="r.key" :class="r.g ? ['row', `t-${r.g.text.tone}`] : 'day'" :style="{ '--i': r.i }">
            <template v-if="r.g">
              <button type="button" class="item" :aria-pressed="sel === r.key" @click="flip(r.key)">
                <span class="time">{{ r.time }}</span>
                <span class="what">
                  <span class="line"><i class="dot" aria-hidden="true" /><b v-if="r.g.e.sym" class="sym">{{ r.g.e.sym }}</b><span class="title">{{ r.g.text.title }}</span><span v-if="r.g.count > 1" class="count" :title="`повторилось ${r.g.count} раз подряд`">×{{ r.g.count }}</span></span>
                  <span v-if="r.g.text.detail" class="detail">{{ r.g.text.detail }}</span>
                </span>
              </button>
            </template>
            <template v-else>{{ r.day }}</template>
          </li>
        </TransitionGroup>
        <p v-else class="empty">Событий пока нет.</p>
      </aside>
    </Transition>

    <button ref="button" type="button" class="fab" :aria-expanded="open" aria-controls="event-panel" :aria-label="`События, новых: ${events.unread}`" @click="toggle">
      <svg viewBox="0 0 64 64" width="26" height="26" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M20 46H46C54 46 58 39 56 32C54 26 48 24 44 26C42 19 36 15 30 17C24 19 22 24 22 27C14 27 10 33 12 39C13 43 16 46 20 46Z" />
      </svg>
      <span v-if="events.unread" class="badge">{{ badge }}</span>
    </button>

    <UiSidePanel :open="!!selRow" label="Событие" @close="sel = null">
      <UiDetail v-if="selRow" label="Событие" @close="sel = null">
        <template #title><b v-if="selRow.e.sym" class="dsym">{{ selRow.e.sym }}</b><span class="dtitle">{{ selRow.text.title }}</span></template>
        <p v-if="selRow.text.detail" class="ddetail">{{ selRow.text.detail }}</p>
        <dl class="dkv"><template v-for="[k, v] in moreOf(selRow)" :key="k"><dt>{{ k }}</dt><dd>{{ v }}</dd></template></dl>
      </UiDetail>
    </UiSidePanel>
  </div>
</template>

<style scoped>
.cloud { position: fixed; right: var(--space-5); bottom: var(--space-5); z-index: 20; display: flex; flex-direction: column; align-items: flex-end; gap: var(--space-3); }
.fab {
  position: relative; width: 48px; height: 48px; display: grid; place-items: center;
  background: var(--panel); color: var(--muted); border: 1px solid var(--line2); border-radius: 50%; cursor: pointer;
  transition: color var(--dur-fast) ease, border-color var(--dur-fast) ease, transform 120ms var(--ease-out);
}
.fab[aria-expanded="true"] { color: var(--accent); border-color: var(--accent); }
.fab:active { transform: scale(0.96); }
@media (hover: hover) and (pointer: fine) {
  .fab:hover { color: var(--text); border-color: var(--muted); }
}
.badge { position: absolute; top: -5px; right: -5px; min-width: 20px; height: 20px; padding: 0 5px; display: grid; place-items: center; border-radius: 99px; background: var(--accent); color: var(--bg); font-size: 11px; font-weight: 700; font-variant-numeric: tabular-nums; }
.panel { width: min(380px, calc(100vw - 2 * var(--space-5))); max-height: min(60vh, 520px); display: flex; flex-direction: column; background: var(--panel); border: 1px solid var(--line2); border-radius: var(--radius); box-shadow: 0 8px 24px rgba(0, 0, 0, 0.28); outline: none; transform-origin: bottom right; }
.head { display: flex; align-items: center; justify-content: space-between; padding: var(--space-3) var(--space-4); border-bottom: 1px solid var(--line); font-weight: 600; font-size: 13px; }
.x { background: transparent; border: 0; color: var(--muted); font-size: 20px; line-height: 1; cursor: pointer; padding: 0 var(--space-1); }
.list { list-style: none; margin: 0; padding: var(--space-1) 0; overflow-y: auto; position: relative; scrollbar-gutter: stable; }
.item { display: grid; grid-template-columns: 58px minmax(0, 1fr); column-gap: var(--space-3); width: 100%; padding: 7px var(--space-3) 7px var(--space-4); background: transparent; border: 0; color: inherit; font: inherit; font-size: 12px; line-height: 1.35; text-align: left; cursor: pointer; transition: background-color var(--dur-fast) ease; }
.item[aria-pressed='true'] { background: var(--panel2); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent) 45%, transparent); }
@media (hover: hover) and (pointer: fine) {
  .item:hover { background: var(--panel2); }
}
.item:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
/* детали события: боковая панель поверх страницы (UiSidePanel) */
.dsym { font-size: 18px; letter-spacing: -0.01em; }
.dtitle { font-size: 14px; font-weight: 600; overflow-wrap: anywhere; }
.ddetail { margin: 0; padding: var(--space-3); border-radius: var(--radius-sm); background: var(--panel2); font-size: 13px; line-height: 1.5; overflow-wrap: anywhere; }
.dkv { display: grid; grid-template-columns: minmax(80px, 0.6fr) minmax(0, 2fr); gap: 5px var(--space-3); margin: 0; font-size: 12px; }
.dkv dt { color: var(--muted); }
.dkv dd { margin: 0; overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }
.time { color: var(--muted); font-variant-numeric: tabular-nums; padding-top: 1px; }
.what { min-width: 0; display: grid; gap: 1px; }
.line { display: flex; align-items: baseline; gap: var(--space-2); min-width: 0; }
.dot { flex: none; align-self: center; width: 6px; height: 6px; border-radius: 50%; background: var(--line2); }
.t-bad .dot { background: var(--neg); }
.t-good .dot { background: var(--pos); }
.sym { font-size: 12px; }
.title { min-width: 0; overflow-wrap: anywhere; }
.count { margin-left: auto; flex: none; padding: 0 6px; border-radius: 99px; background: var(--panel2); color: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }
.detail { color: var(--muted); overflow-wrap: anywhere; }
.day { padding: var(--space-3) var(--space-4) var(--space-1); font-size: 11px; color: var(--muted); }
.empty { margin: 0; padding: var(--space-5); text-align: center; color: var(--muted); font-size: 13px; }

/* Открытие: панель вырастает из кнопки (origin в правом нижнем углу), строки следом с шагом 30 мс (первые девять). Закрытие быстрее и без строк: сворачивается обратно в кнопку.
   Действие редкое (оно же ниже порога 100 раз в день), поэтому движение допустимо; кривые только из motion.css. */
.panel-enter-from, .panel-leave-to { opacity: 0; transform: translateY(10px) scale(0.92); }
.panel-enter-active { transition: opacity var(--dur-enter) var(--ease-out), transform 260ms var(--ease-out); }
.panel-leave-active { transition: opacity 120ms var(--ease-out), transform 160ms var(--ease-out); }
.panel-enter-from .row, .panel-enter-from .day { opacity: 0; transform: translateY(6px); }
.panel-enter-active .row, .panel-enter-active .day { transition: opacity var(--dur-enter) var(--ease-out), transform var(--dur-enter) var(--ease-out); transition-delay: calc(var(--i, 0) * 30ms + 60ms); }
@media (prefers-reduced-motion: reduce) {
  .panel-enter-from, .panel-leave-to { transform: none; }
  .panel-enter-from .row, .panel-enter-from .day { transform: none; }
  .fab:active { transform: none; }
}
</style>
