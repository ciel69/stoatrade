<script setup lang="ts">
import { computed } from 'vue'
import { dayFacts, dayHeadline, useDayStore } from '@/entities/day'
import { useLive } from '@/shared/api'
import { dayLabel, dayModeWord, dayStateWord, useNow } from '@/shared/lib'

/**
 * Начало страницы «День»: одно предложение о том, что происходит и нужно ли что-то от вас, под ним уточнение и два-три факта (итог дня, план дня, перенесённое со вчера).
 * Цвет точки: зелёный (всё в порядке), жёлтый (нужен ответ или внимание), красный (сбой), серый (спокойно или данных нет). Данные приходят по WebSocket темой day.
 */
const store = useDayStore()
const { link } = useLive()
const now = useNow()
const snap = computed(() => store.snapshot)
const head = computed(() => dayHeadline(snap.value, now.value))
const c = computed(() => snap.value?.current ?? null)
const facts = computed(() => dayFacts(c.value))
const sub = computed(() => (c.value ? [dayLabel(c.value.day_id), dayModeWord(c.value.mode), dayStateWord(c.value.state)].join(' · ') : ''))
const offline = computed(() => !snap.value && link.value !== 'open')
</script>

<template>
  <section class="hero" :class="`lv-${offline ? 'bad' : head.tone}`" aria-label="Торговый день">
    <template v-if="snap">
      <div class="row">
        <i class="dot" aria-hidden="true" />
        <div class="txt">
          <h2 class="title" role="status">{{ head.text }}</h2>
          <p v-if="head.detail" class="detail">{{ head.detail }}</p>
        </div>
      </div>
      <p v-if="sub" class="sub">{{ sub }}<span v-if="c?.day_id" class="id"> · {{ c.day_id }}</span></p>
      <dl v-if="facts.length" class="facts">
        <div v-for="f in facts" :key="f.label" class="fact"><dt>{{ f.label }}</dt><dd>{{ f.text }}</dd></div>
      </dl>
    </template>
    <div v-else-if="offline" class="row">
      <i class="dot" aria-hidden="true" />
      <div class="txt">
        <h2 class="title" role="status">Нет связи с платформой</h2>
        <p class="detail">Данные о торговом дне не приходят. Страница переподключится сама; если не получается, проверьте, что платформа запущена.</p>
      </div>
    </div>
    <div v-else class="skel" aria-busy="true" />
  </section>
</template>

<style scoped>
.hero { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: var(--space-5); display: grid; gap: var(--space-3); }
.row { display: flex; align-items: flex-start; gap: var(--space-4); }
.dot { flex: none; width: 12px; height: 12px; margin-top: 8px; border-radius: 50%; background: var(--pos); transition: background-color var(--dur-fast) ease; }
.lv-warn .dot { background: var(--warn); }
.lv-bad .dot { background: var(--neg); }
.lv-idle .dot { background: var(--muted); }
.txt { min-width: 0; display: grid; gap: var(--space-1); }
.title { margin: 0; font-size: 22px; font-weight: 700; line-height: 1.3; letter-spacing: -0.01em; text-wrap: balance; }
.detail { margin: 0; font-size: 14px; line-height: 1.5; color: var(--muted); max-width: 70ch; text-wrap: pretty; }
.sub { margin: 0 0 0 calc(12px + var(--space-4)); font-size: 12px; color: var(--muted); }
.id { font-family: var(--font-mono); font-size: 11px; }
.facts { margin: 0 0 0 calc(12px + var(--space-4)); display: flex; flex-wrap: wrap; gap: var(--space-2) var(--space-6); padding-top: var(--space-3); border-top: 1px solid var(--line); }
.fact { display: grid; gap: 2px; min-width: 0; }
.fact dt { font-size: 12px; color: var(--muted); }
.fact dd { margin: 0; font-size: 14px; font-weight: 600; font-variant-numeric: tabular-nums; text-wrap: pretty; }
.skel { height: 96px; border-radius: var(--radius-sm); background: var(--panel2); }
@media (max-width: 520px) {
  .hero { padding: var(--space-4); }
  .title { font-size: 19px; }
  .sub, .facts { margin-left: 0; }
}
</style>
