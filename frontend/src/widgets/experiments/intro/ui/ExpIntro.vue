<script setup lang="ts">
import { computed } from 'vue'
import { expHeadline, tradesText, useExperimentStore } from '@/entities/experiment'
import { useLive } from '@/shared/api'
import { fixed, signed } from '@/shared/lib'

/**
 * Начало страницы «Эксперименты»: главный вывод одной строкой («Подтверждённых гипотез: N» или «пока нет»), список подтверждённых и три фразы о том, как читать цифры
 * (что такое гипотеза и эксперимент, почему мало сделок ничего не доказывают, зачем поправка на число сравнений). Подтверждение считается на бумаге по правилу досье мозга и не зависит от выбранного разреза.
 */
const store = useExperimentStore()
const { link } = useLive()
const d = computed(() => store.detail)
const head = computed(() => expHeadline(d.value))
const confirmed = computed(() => d.value?.confirmed ?? [])
const offline = computed(() => !d.value && link.value !== 'open' && !store.mocked)
const failed = computed(() => !!d.value && !d.value.ok)
const pct = (x: number | null): string => (typeof x === 'number' ? `${Math.round(x * 100)}%` : '–')
</script>

<template>
  <section class="intro" :class="head ? `lv-${head.tone}` : offline || failed ? 'lv-bad' : 'lv-idle'" aria-labelledby="ei-h">
    <div class="row">
      <i class="dot" aria-hidden="true" />
      <div class="txt">
        <h2 id="ei-h" class="title" role="status">
          <template v-if="head">{{ head.text }}</template>
          <template v-else-if="offline">Нет связи с платформой</template>
          <template v-else-if="failed">Не удалось собрать статистику</template>
          <template v-else>Считаем статистику</template>
        </h2>
        <p v-if="head" class="detail">{{ head.detail }}</p>
        <p v-else-if="offline" class="detail">Данные о сделках не приходят. Страница переподключится сама; если не получается, проверьте, что платформа запущена.</p>
        <p v-else-if="failed" class="detail">Журнал сделок не прочитался. Попробуйте другой разрез или зайдите позже: ничего не потеряно.</p>
      </div>
    </div>

    <ul v-if="confirmed.length" class="conf" aria-label="Подтверждённые гипотезы">
      <li v-for="c in confirmed" :key="c.hyp" class="c">
        <b class="name">{{ c.hyp }}</b>
        <span class="facts">
          <span>{{ tradesText(c.n) }}</span>
          <span class="pos">{{ signed(c.avg_r, 2) }} R в среднем</span>
          <span>прибыльных {{ pct(c.win_rate) }}</span>
          <span v-if="c.live_n" class="live">на реальные деньги: {{ tradesText(c.live_n) }}, {{ typeof c.live_avg_r === 'number' ? `${signed(c.live_avg_r, 2)} R` : fixed(null) }}</span>
        </span>
      </li>
    </ul>

    <div class="how">
      <p><b>Гипотеза</b> это идея, которую проверяют серией сделок. <b>Эксперимент</b> сравнивает несколько <b>вариантов</b> одной идеи на одинаковых условиях: например, стоп поближе и подальше.</p>
      <p>Несколько удачных сделок ничего не доказывают: серия может выпасть случайно. Поэтому группы меньше 30 сделок помечены «мало данных», и вывод по ним делать рано.</p>
      <p>Чем больше групп сравнивают сразу, тем чаще одна из них выглядит удачной просто так. Поэтому порог для вывода строже: это поправка на число сравнений. Результат в <b>R</b>: +1 R значит, что заработали столько, сколько рисковали.</p>
    </div>
  </section>
</template>

<style scoped>
.intro { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: var(--space-5); display: grid; gap: var(--space-4); }
.row { display: flex; align-items: flex-start; gap: var(--space-4); }
.dot { flex: none; width: 12px; height: 12px; margin-top: 8px; border-radius: 50%; background: var(--pos); transition: background-color var(--dur-fast) ease; }
.lv-idle .dot { background: var(--muted); }
.lv-bad .dot { background: var(--neg); }
.txt { min-width: 0; display: grid; gap: var(--space-1); }
.title { margin: 0; font-size: 22px; font-weight: 700; line-height: 1.3; letter-spacing: -0.01em; text-wrap: balance; }
.detail { margin: 0; font-size: 14px; line-height: 1.5; color: var(--muted); max-width: 70ch; text-wrap: pretty; }
.conf { list-style: none; margin: 0 0 0 calc(12px + var(--space-4)); padding: 0; display: grid; border-top: 1px solid var(--line); }
.c { display: flex; flex-wrap: wrap; align-items: baseline; gap: var(--space-1) var(--space-4); padding: var(--space-2) 0; border-bottom: 1px solid var(--line); font-size: 13px; }
.c:last-child { border-bottom: 0; }
.name { overflow-wrap: anywhere; }
.facts { display: flex; flex-wrap: wrap; gap: 0 var(--space-4); color: var(--muted); font-variant-numeric: tabular-nums; }
.pos { color: var(--pos); font-weight: 600; }
.how { margin: 0 0 0 calc(12px + var(--space-4)); display: grid; gap: var(--space-2); max-width: 78ch; padding-top: var(--space-3); border-top: 1px solid var(--line); }
.how p { margin: 0; font-size: 13px; line-height: 1.55; color: var(--muted); text-wrap: pretty; }
.how b { color: var(--text); font-weight: 600; }
@media (max-width: 520px) {
  .intro { padding: var(--space-4); }
  .title { font-size: 19px; }
  .conf, .how { margin-left: 0; }
}
</style>
