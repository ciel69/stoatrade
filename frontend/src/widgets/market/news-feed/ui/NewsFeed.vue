<script setup lang="ts">
import { computed, ref } from 'vue'
import { dirTone, filterNews, importance, markState, NEWS_DIR, NEWS_TYPE, useNewsStore, weightOf, type NewsFilter, type NewsItem } from '@/entities/headline'
import { UiChip, UiSegment } from '@/shared/ui'
import { ageText, countdown, fixed, useNow } from '@/shared/lib'

/**
 * Лента новостей в «Рынке»: очищенные заголовки с разметкой карантинного читателя (тип, важность, направление, монеты). Слева полоса важности (красная от 4, янтарная 3). Монета в разметке
 * выбирает её на карте. Сверху календарь событий и окно блокировки. Заголовки это текст третьих лиц: показываются как текст, мозгу не передаются, ссылок нет (только домен).
 */
const props = defineProps<{ coin: string | null }>()
const emit = defineEmits<{ selectCoin: [sym: string] }>()
const store = useNewsStore()
const now = useNow()

const filter = ref<NewsFilter>('all')
const open = ref<string | null>(null)
const limit = ref(15)
const snap = computed(() => store.snapshot)
const all = computed(() => snap.value?.items ?? [])
const options = computed(() => [
  { value: 'all' as NewsFilter, label: `Все${snap.value ? ' ' + all.value.length : ''}` },
  { value: 'important' as NewsFilter, label: `Важные ${filterNews(all.value, 'important', null).length}` },
  ...(props.coin ? [{ value: 'coin' as NewsFilter, label: `${props.coin} ${filterNews(all.value, 'coin', props.coin).length}` }] : []),
])
const active = computed<NewsFilter>(() => (filter.value === 'coin' && !props.coin ? 'all' : filter.value))
const rows = computed(() => filterNews(all.value, active.value, props.coin))
const shown = computed(() => rows.value.slice(0, limit.value))
const badSources = computed(() => (snap.value?.sources ?? []).filter((s) => s.degraded || !s.ok))
const events = computed(() => (snap.value?.calendar.next ?? []).slice(0, 3))
const blackout = computed(() => snap.value?.calendar.blackout ?? null)

const markText = (i: NewsItem): string => ({ ok: '', struct: 'структурно (факты)', waiting: 'ждёт читателя', susp: 'подозрение: текст модели не передавался', fail: 'формат разметки не принят', raw: 'без модели' })[markState(i)]
const typeText = (i: NewsItem): string => (i.cls?.type ? (NEWS_TYPE[i.cls.type] ?? i.cls.type) : '')
const toggle = (ref_: string): void => { open.value = open.value === ref_ ? null : ref_ }
const when = (s: number): string => { const d = s - now.value; return d > 86400 ? `через ${Math.round(d / 86400)} дн` : `через ${countdown(d)}` }
</script>

<template>
  <section class="news" aria-label="Новости">
    <header class="top">
      <h2 class="title">Новости</h2>
      <span v-if="snap && !snap.reader.enabled" class="note" title="config/intel.json, reader.enabled">читатель выключен: заголовки без разметки</span>
      <span v-else-if="snap?.reader.stale" class="note warn">разметка устарела</span>
      <UiSegment v-model="filter" :options="options" label="Фильтр новостей" />
    </header>

    <p v-if="blackout" class="black"><UiChip tone="warn">окно блокировки</UiChip> вокруг события «{{ blackout.name || 'без названия' }}»: новые входы закрыты</p>
    <ul v-if="events.length" class="cal" aria-label="Календарь событий">
      <li v-for="e in events" :key="e.id" :title="e.verified ? 'дата проверена' : 'дата не проверена'">
        <b>{{ e.type }}</b> {{ when(e.at) }} <span class="muted">влияние {{ e.impact ?? '–' }}{{ e.verified ? '' : ', дата не проверена' }}</span>
      </li>
    </ul>
    <p v-if="badSources.length" class="srcs warn">Источники с проблемами: {{ badSources.map((s) => s.id).join(', ') }}</p>

    <template v-if="snap">
      <ul v-if="shown.length" class="list">
        <li v-for="i in shown" :key="i.ref" class="item" :class="`w-${weightOf(importance(i))}`">
          <button type="button" class="hit" :aria-expanded="open === i.ref" @click="toggle(i.ref)">
            <span class="meta">{{ ageText(i.ts, now) }} · {{ i.src }}</span>
            <span class="head">{{ i.title }}</span>
          </button>
          <span class="chips">
            <template v-if="markState(i) === 'ok' && i.cls">
              <UiChip>{{ typeText(i) }}</UiChip>
              <UiChip :tone="importance(i) >= 4 ? 'bad' : importance(i) >= 3 ? 'warn' : 'neutral'" :title="i.cls.ir !== i.cls.imp ? `читатель дал ${i.cls.ir}, понижено правилом подтверждения` : ''">важн. {{ importance(i) }}</UiChip>
              <span v-if="i.cls.dir" class="dir" :class="dirTone(i.cls)">{{ NEWS_DIR[i.cls.dir] ?? i.cls.dir }}</span>
              <button v-for="s in i.cls.syms" :key="s" type="button" class="sym" @click="emit('selectCoin', s)">{{ s }}</button>
            </template>
            <UiChip v-else-if="markState(i) === 'susp'" tone="bad">подозрение</UiChip>
            <span v-else class="muted">{{ markText(i) }}</span>
          </span>
          <div v-if="open === i.ref" class="det">
            <p v-if="i.snip" class="snip">{{ i.snip }}</p>
            <dl class="kv">
              <div><dt>Источник</dt><dd>{{ i.src }}{{ i.domain ? ' · ' + i.domain : '' }}</dd></div>
              <div><dt>Подтверждений с других доменов</dt><dd>{{ i.seen }}</dd></div>
              <template v-if="i.cls && i.cls.st === 'ok'">
                <div><dt>Актуально</dt><dd>{{ countdown((i.cls.ttl_min ?? 0) * 60) === '–' ? '–' : (i.cls.ttl_min ?? 0) + ' мин' }}, уверенность {{ fixed(i.cls.conf, 2) }}</dd></div>
              </template>
              <div v-if="i.susp"><dt>Фильтр инъекций</dt><dd>сработал: текст не передавался модели, важность 1</dd></div>
              <div><dt>Идентификатор</dt><dd class="mono">{{ i.ref }}</dd></div>
            </dl>
          </div>
        </li>
      </ul>
      <p v-else class="empty">{{ all.length ? 'В этом фильтре заголовков нет.' : 'Заголовков пока нет: их собирает сервис intel из RSS (./tb up --services ...,intel).' }}</p>
      <button v-if="rows.length > limit" type="button" class="more" @click="limit += 20">Показать ещё {{ Math.min(20, rows.length - limit) }}</button>
    </template>
    <div v-else class="skel" aria-busy="true" />
  </section>
</template>

<style scoped>
.news { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: var(--space-4); min-width: 0; display: grid; gap: var(--space-3); }
.top { display: flex; align-items: center; flex-wrap: wrap; gap: var(--space-3); }
.title { margin: 0; font-size: 14px; font-weight: 600; margin-right: auto; }
.note { font-size: 12px; color: var(--muted); }
.warn { color: var(--warn); }
.black { margin: 0; font-size: 12px; display: flex; align-items: center; gap: var(--space-2); }
.cal { list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: var(--space-2) var(--space-5); font-size: 12px; font-variant-numeric: tabular-nums; }
.muted { color: var(--muted); }
.srcs { margin: 0; font-size: 12px; }
.list { list-style: none; margin: 0; padding: 0; }
.item { position: relative; padding: var(--space-3) 0 var(--space-3) var(--space-3); border-top: 1px solid var(--line); }
.item:first-child { border-top: 0; }
.item::before { content: ''; position: absolute; left: 0; top: var(--space-3); bottom: var(--space-3); width: 3px; border-radius: 2px; background: var(--line2); }
.item.w-mid::before { background: var(--warn); }
.item.w-high::before { background: var(--neg); }
.hit { display: grid; gap: 2px; width: 100%; padding: 0; border: 0; background: none; color: inherit; font: inherit; text-align: left; cursor: pointer; border-radius: var(--radius-sm); }
.hit:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
.meta { font-size: 11px; color: var(--muted); }
.head { font-size: 14px; line-height: 1.35; display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.chips { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-2); margin-top: 6px; font-size: 12px; }
.dir { color: var(--muted); }
.dir.up { color: var(--pos); }
.dir.down { color: var(--neg); }
.sym { padding: 1px 8px; border: 1px solid var(--line2); border-radius: 99px; background: none; color: var(--text); font: inherit; font-size: 11px; font-weight: 600; cursor: pointer; }
.sym:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.det { margin-top: var(--space-3); padding: var(--space-3); border-radius: var(--radius-sm); background: var(--panel2); display: grid; gap: var(--space-2); font-size: 12px; }
.snip { margin: 0; font-size: 13px; line-height: 1.45; }
.kv { margin: 0; display: grid; gap: 4px; }
.kv > div { display: flex; gap: var(--space-3); justify-content: space-between; }
dt { color: var(--muted); }
dd { margin: 0; text-align: right; overflow-wrap: anywhere; }
.mono { font-family: var(--mono, ui-monospace, monospace); font-size: 11px; }
.empty { margin: 0; font-size: 13px; color: var(--muted); max-width: 60ch; }
.more { justify-self: start; padding: 6px 14px; border: 1px solid var(--line2); border-radius: var(--radius-sm); background: var(--panel2); color: var(--text); font: inherit; font-size: 12px; cursor: pointer; }
.more:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.skel { height: 120px; border-radius: var(--radius-sm); background: var(--panel2); }
@media (hover: hover) and (pointer: fine) {
  .hit:hover .head { color: var(--accent); }
  .sym:hover, .more:hover { border-color: var(--accent); }
}
</style>
