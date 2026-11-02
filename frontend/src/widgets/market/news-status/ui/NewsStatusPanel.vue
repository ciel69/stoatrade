<script setup lang="ts">
import { computed } from 'vue'
import { useActionStore } from '@/entities/action'
import { blackoutView, eventViews, readerView, sourceViews, useNewsStore } from '@/entities/headline'
import { UiDetail, UiStatusDot } from '@/shared/ui'
import { READER_ENABLE_HINT, useNow } from '@/shared/lib'

/**
 * Боковая панель «Состояние новостей»: читатель (включён ли, что делает, расход), источники (работают ли, проверены ли) и календарь (ближайшие события, окно блокировки).
 * «Обновить» просит платформу перечитать источник (intel.refresh): безрисковое, без диалога, итог приходит уведомлением; кнопка есть только при расширенном управлении.
 * Названия источников и событий это данные из конфигурации: показываются как текст.
 */
defineEmits<{ close: [] }>()
const store = useNewsStore()
const action = useActionStore()
const now = useNow()

const snap = computed(() => store.snapshot)
const reader = computed(() => (snap.value ? readerView(snap.value, now.value) : null))
const sources = computed(() => (snap.value ? sourceViews(snap.value, now.value) : []))
const events = computed(() => (snap.value ? eventViews(snap.value, now.value).slice(0, 5) : []))
const blackout = computed(() => (snap.value ? blackoutView(snap.value, now.value) : null))
const canRefresh = computed(() => action.can('intel.refresh'))

function refresh(id: string): void {
  if (action.active || !canRefresh.value) return
  action.run({ action: 'intel.refresh', args: { source: id }, title: `Обновить источник ${id}`, what: 'Платформа перечитает ленту источника сейчас, не дожидаясь очередного цикла.' })
}
function refreshAll(): void {
  if (action.active || !canRefresh.value) return
  action.run({ action: 'intel.refresh', args: { source: 'all' }, title: 'Обновить все источники', what: 'Платформа перечитает все ленты новостей сейчас, не дожидаясь очередного цикла.' })
}
</script>

<template>
  <UiDetail label="Состояние новостей" @close="$emit('close')">
    <template #title><h2 class="ttl">Состояние новостей</h2></template>

    <p v-if="!snap || !reader" class="muted">Данных пока нет.</p>
    <template v-else>
      <section class="blk" aria-labelledby="ns-reader">
        <h3 id="ns-reader">Читатель заголовков</h3>
        <p class="state"><UiStatusDot :state="reader.tone" :label="reader.text" /> <b>{{ reader.stale ? 'разметка устарела' : reader.text }}</b></p>
        <p class="about">{{ reader.about }}</p>
        <dl v-if="reader.lines.length" class="kv">
          <div v-for="l in reader.lines" :key="l.label"><dt>{{ l.label }}</dt><dd>{{ l.value }}</dd></div>
        </dl>
      </section>

      <section class="blk" aria-labelledby="ns-src">
        <div class="row">
          <h3 id="ns-src">Источники</h3>
          <button v-if="sources.length && canRefresh" type="button" class="btn" :disabled="action.active" @click="refreshAll">Обновить все</button>
        </div>
        <ul v-if="sources.length" class="list">
          <li v-for="s in sources" :key="s.id" class="line">
            <UiStatusDot :state="s.tone" :label="s.state" />
            <span class="main"><b class="name">{{ s.id }}</b> <span class="muted">{{ s.state }} · {{ s.verified }}{{ s.seen ? ' · ' + s.seen : '' }}</span></span>
            <button v-if="canRefresh && s.tone === 'warn'" type="button" class="btn sm" :disabled="action.active" :aria-label="`Обновить источник ${s.id}`" @click="refresh(s.id)">Обновить</button>
          </li>
        </ul>
        <p v-else class="muted">Служба новостей пока не сообщала об источниках.</p>
      </section>

      <section class="blk" aria-labelledby="ns-cal">
        <h3 id="ns-cal">Календарь событий</h3>
        <p v-if="blackout" class="black">{{ blackout.text }}{{ blackout.verified ? '' : ' Дата события не проверена, окно поставлено на всякий случай.' }}</p>
        <ul v-if="events.length" class="list">
          <li v-for="e in events" :key="e.id" class="line">
            <span class="main"><b class="name">{{ e.type }}</b> <span class="muted">{{ e.inText }} · {{ e.impact }}{{ e.verified ? '' : ' · дата не проверена' }}</span></span>
          </li>
        </ul>
        <p v-else-if="!blackout" class="muted">Ближайших событий нет.</p>
      </section>

      <details class="spec">
        <summary>Для специалиста</summary>
        <dl class="kv">
          <div><dt>Статус читателя</dt><dd>{{ snap.reader.status ?? 'нет' }}</dd></div>
          <div v-if="reader.err"><dt>Последняя ошибка модели</dt><dd>{{ reader.err }}</dd></div>
        </dl>
        <p class="about">{{ READER_ENABLE_HINT }}</p>
      </details>
    </template>
  </UiDetail>
</template>

<style scoped>
.ttl { margin: 0; font-size: 15px; font-weight: 600; }
.blk { display: grid; gap: var(--space-2); }
.blk h3 { margin: 0; font-size: 13px; font-weight: 600; }
.row { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); }
.state { margin: 0; font-size: 14px; display: flex; align-items: center; gap: var(--space-2); }
.about { margin: 0; font-size: 13px; line-height: 1.5; color: var(--muted); max-width: 60ch; }
.muted { color: var(--muted); font-size: 12px; margin: 0; }
.black { margin: 0; font-size: 13px; line-height: 1.5; }
.kv { margin: 0; display: grid; gap: 4px; font-size: 12px; }
.kv > div { display: flex; flex-wrap: wrap; gap: 0 var(--space-3); justify-content: space-between; }
dt { color: var(--muted); flex: none; }
dd { margin: 0; text-align: right; overflow-wrap: anywhere; flex: 1 1 auto; min-width: 0; }
.list { list-style: none; margin: 0; padding: 0; display: grid; }
.line { display: flex; align-items: center; gap: var(--space-3); min-height: 40px; padding: 6px 0; border-bottom: 1px solid var(--line); font-size: 13px; }
.line:last-child { border-bottom: 0; }
.main { flex: 1 1 auto; min-width: 0; overflow-wrap: anywhere; }
.name { font-weight: 600; margin-right: var(--space-2); }
.btn { flex: none; min-height: 36px; padding: 0 var(--space-3); font: inherit; font-size: 13px; color: var(--text); background: transparent; border: 1px solid var(--line2); border-radius: var(--radius-sm); cursor: pointer; white-space: nowrap; transition: color var(--dur-fast) ease, border-color var(--dur-fast) ease, opacity var(--dur-fast) ease, transform 120ms var(--ease-out); }
.btn.sm { min-height: 32px; font-size: 12px; }
.btn:disabled { opacity: 0.45; cursor: not-allowed; }
.btn:active:not(:disabled) { transform: scale(0.97); }
.btn:focus-visible, .spec summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.spec { font-size: 12px; display: grid; gap: var(--space-2); }
.spec summary { cursor: pointer; color: var(--muted); min-height: 32px; display: flex; align-items: center; }
.spec[open] { gap: var(--space-2); }
@media (hover: hover) and (pointer: fine) {
  .btn:hover:not(:disabled) { border-color: var(--muted); }
}
@media (prefers-reduced-motion: reduce) {
  .btn { transition: color var(--dur-fast) ease, border-color var(--dur-fast) ease, opacity var(--dur-fast) ease; }
  .btn:active:not(:disabled) { transform: none; }
}
</style>
