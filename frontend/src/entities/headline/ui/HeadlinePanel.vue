<script setup lang="ts">
import { UiChip, UiDetail } from '@/shared/ui'
import { ageText, fixed, useNow } from '@/shared/lib'
import { dirTone, importance, markState, NEWS_DIR, NEWS_TYPE } from '../lib/news'
import type { NewsItem } from '../model/types'

/**
 * Боковая панель новости: заголовок и выдержка (текст третьих лиц, только как текст), разметка читателя (тип, важность, направление, монеты), источник и подтверждения.
 * Монета в разметке выбирает её на карте (select-coin). Ссылок нет: показан только домен.
 */
defineProps<{ item: NewsItem }>()
defineEmits<{ close: []; selectCoin: [sym: string] }>()
const now = useNow()
</script>

<template>
  <UiDetail label="Новость" @close="$emit('close')">
    <template #title>
      <span class="meta">{{ ageText(item.ts, now) }} · {{ item.src }}{{ item.domain ? ' · ' + item.domain : '' }}</span>
    </template>
    <h3 class="head">{{ item.title }}</h3>
    <p v-if="item.snip" class="snip">{{ item.snip }}</p>

    <section class="mark">
      <template v-if="markState(item) === 'ok' && item.cls">
        <div class="chips">
          <UiChip>{{ item.cls.type ? (NEWS_TYPE[item.cls.type] ?? item.cls.type) : '' }}</UiChip>
          <UiChip :tone="importance(item) >= 4 ? 'bad' : importance(item) >= 3 ? 'warn' : 'neutral'">важность {{ importance(item) }}</UiChip>
          <span v-if="item.cls.dir" class="dir" :class="dirTone(item.cls)">{{ NEWS_DIR[item.cls.dir] ?? item.cls.dir }}</span>
        </div>
        <div v-if="item.cls.syms.length" class="chips"><button v-for="s in item.cls.syms" :key="s" type="button" class="sym" @click="$emit('selectCoin', s)">{{ s }}</button></div>
        <dl class="kv">
          <div v-if="item.cls.ir !== item.cls.imp"><dt>Читатель дал</dt><dd>{{ item.cls.ir }} (понижено правилом подтверждения)</dd></div>
          <div><dt>Актуально</dt><dd>{{ item.cls.ttl_min ?? 0 }} мин, уверенность {{ fixed(item.cls.conf, 2) }}</dd></div>
        </dl>
      </template>
      <p v-else class="muted">{{ ({ ok: '', struct: 'Структурный источник (факты): читатель не размечает.', waiting: 'Ждёт разметки читателем.', susp: 'Подозрение: сработал фильтр инъекций, текст модели не передавался, важность 1.', fail: 'Формат разметки читателя не принят.', raw: 'Без модели: фильтр инъекций сработал, важность 1.' })[markState(item)] }}</p>
    </section>

    <dl class="kv">
      <div><dt>Подтверждений с других доменов</dt><dd>{{ item.seen }}</dd></div>
      <div><dt>Идентификатор</dt><dd class="mono">{{ item.ref }}</dd></div>
    </dl>
  </UiDetail>
</template>

<style scoped>
.meta { font-size: 12px; color: var(--muted); }
.head { margin: 0; font-size: 17px; line-height: 1.35; font-weight: 600; overflow-wrap: anywhere; }
.snip { margin: 0; font-size: 13px; line-height: 1.5; color: var(--text); overflow-wrap: anywhere; }
.mark { display: grid; gap: var(--space-3); padding: var(--space-3); border-radius: var(--radius-sm); background: var(--panel2); }
.chips { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-2); font-size: 12px; }
.dir { color: var(--muted); }
.dir.up { color: var(--pos); }
.dir.down { color: var(--neg); }
.sym { padding: 2px 10px; border: 1px solid var(--line2); border-radius: 99px; background: none; color: var(--text); font: inherit; font-size: 12px; font-weight: 600; cursor: pointer; }
.sym:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.kv { margin: 0; display: grid; gap: 4px; font-size: 12px; }
.kv > div { display: flex; gap: var(--space-3); justify-content: space-between; }
dt { color: var(--muted); }
dd { margin: 0; text-align: right; overflow-wrap: anywhere; }
.mono { font-family: var(--mono, ui-monospace, monospace); font-size: 11px; }
.muted { margin: 0; font-size: 12px; color: var(--muted); }
@media (hover: hover) and (pointer: fine) {
  .sym:hover { border-color: var(--accent); }
}
</style>
