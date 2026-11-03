<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  blockView, isLearningMode, isStale, learningState, updatedText, useLearningStore,
  type DisciplineBlock, type ExitsBlock, type LearningBlock, type LearningMode, type SkipsBlock,
} from '@/entities/learning'
import { useTradingStore } from '@/entities/trading'
import { LearningDiscipline } from '@/widgets/learning/discipline'
import { LearningEconomics } from '@/widgets/learning/economics'
import { LearningExits } from '@/widgets/learning/exits'
import { LearningHyps } from '@/widgets/learning/hyps'
import { LearnIntro } from '@/widgets/learning/intro'
import { LearningLessons } from '@/widgets/learning/lessons'
import { LearningMemory } from '@/widgets/learning/memory'
import { LearnResult } from '@/widgets/learning/result'
import { LearningSkips } from '@/widgets/learning/skips'
import { Stages } from '@/widgets/learning/stages'
import { LearnVerdict } from '@/widgets/learning/verdict'
import { LearningVersions } from '@/widgets/learning/versions'
import { VsPolicy } from '@/widgets/learning/vs-policy'
import { useLive } from '@/shared/api'
import { LEARN_LOADING, LEARN_NO_LINK, LEARN_REPORT_FAILED, TABS_FULL_TEXT, TABS_FULL_TITLE, modeName, useNow } from '@/shared/lib'
import { UiChip } from '@/shared/ui'

/**
 * Страница «Мозг · Учится ли мозг»: сверху вердикт (одно из трёх слов), справка, затем блоки в порядке `order` ответа; блоки следующих этапов собраны в конце.
 * Блоки R2.6 «Уроки», «Версии мозга до/после» и «Гипотезы» до появления таблиц цикла приходят со статусом stage и тоже уходят в конец («появится на этапе 2»).
 * Режим берётся из переключателя в шапке (один режим, режимы не смешиваются; «все» показывается как бумага). Только чтение.
 * Запрос уходит при заходе, смене режима и закрытии новой сделки, без таймеров; запоздавший ответ другого режима стор отбрасывает.
 * Повторное сообщение того же режима заменяет прежнее, так готовый блок «Считается…» меняется на данные сам. Отладка без сервера: ?learning=mock, mock-few, mock-confirmed, mock-empty, mock-computing.
 */
const store = useLearningStore()
const trading = useTradingStore()
const { link, full } = useLive()
const now = useNow()
store.start()                // до первого запроса: образец ?learning=mock должен быть известен

const mode = computed<LearningMode>(() => (isLearningMode(trading.mode) ? trading.mode : 'paper'))

const live = () => link.value === 'open' || !!store.mocked
// заход, смена режима, возвращение связи
watch(
  [mode, link],
  ([m]) => { if (live()) store.request(m) },
  { immediate: true, flush: 'post' },
)
// закрытие новой сделки: только рост счётчика у уже известного значения; первое появление trades.total (undefined -> число) не запрос
watch(
  () => trading.trades?.total,
  (n, o) => { if (typeof n === 'number' && typeof o === 'number' && n !== o && live()) store.request(mode.value) },
  { flush: 'post' },
)

const d = computed(() => store.detail)
const state = computed(() => learningState(d.value, store.pending, link.value === 'open' || !!store.mocked))

// Сколько секунд блок «Считается…» уже ждёт: отсчёт начинается с первого ответа с таким блоком и сбрасывается, когда таких блоков не осталось.
const computingSince = ref<number | null>(null)
watch(
  () => d.value,
  (v) => {
    const has = !!v && Object.values(v.blocks).some((b) => b.status === 'computing')
    if (!has) computingSince.value = null
    else if (computingSince.value === null) computingSince.value = now.value
  },
  { immediate: true },
)
const waited = computed(() => (computingSince.value === null ? 0 : Math.max(0, now.value - computingSince.value)))

const ordered = computed<LearningBlock[]>(() => (d.value ? d.value.order.map((id) => d.value!.blocks[id]).filter((b): b is LearningBlock => !!b) : []))
const main = computed(() => ordered.value.filter((b) => b.status !== 'stage'))
const stages = computed(() => ordered.value.filter((b) => b.status === 'stage'))

const KNOWN = new Set(['result', 'vs_policy', 'discipline', 'skips', 'exits', 'economics', 'memory', 'lessons', 'versions', 'hyps'])
const asDiscipline = (b: LearningBlock): DisciplineBlock => b as DisciplineBlock
const asSkips = (b: LearningBlock): SkipsBlock => b as SkipsBlock
const asExits = (b: LearningBlock): ExitsBlock => b as ExitsBlock
const plainMessage = (b: LearningBlock): string => blockView(b, waited.value).message

const modeLine = computed(() => (trading.mode === 'all'
  ? `Режимы не смешиваются, поэтому при выборе «все» показан режим: ${modeName('paper')}.`
  : `Режим: ${modeName(mode.value)}. Меняется переключателем в шапке.`))
const updated = computed(() => updatedText(d.value?.asof, now.value))
const stale = computed(() => state.value === 'ok' && isStale(d.value?.asof, now.value))
const linkText = computed(() => (full.value ? `${TABS_FULL_TITLE}, данные не приходят. ${TABS_FULL_TEXT}` : `${LEARN_NO_LINK}, данные не приходят. Страница переподключится сама.`))
</script>

<template>
  <main class="page learn" :aria-busy="state === 'loading'">
    <h1 class="sr">Учится ли мозг</h1>

    <Transition name="fade" mode="out-in">
      <div v-if="state === 'ok' && d" :key="d.mode" class="stack">
        <p class="meta">
          <span>{{ modeLine }}</span>
          <span v-if="updated" class="age">{{ updated }}</span>
          <UiChip v-if="stale" tone="warn">данные устарели</UiChip>
        </p>

        <LearnVerdict :verdict="d.verdict" />
        <LearnIntro />

        <div v-for="(b, i) in main" :key="b.id" class="blk" :style="{ '--i': Math.min(i, 5) }">
          <LearnResult v-if="b.id === 'result'" :block="b" />
          <VsPolicy v-else-if="b.id === 'vs_policy'" :block="b" :min-n="d.min_n" :computing-for-s="waited" />
          <LearningDiscipline v-else-if="b.id === 'discipline'" :block="asDiscipline(b)" :computing-for-s="waited" />
          <LearningSkips v-else-if="b.id === 'skips'" :block="asSkips(b)" :computing-for-s="waited" />
          <LearningExits v-else-if="b.id === 'exits'" :block="asExits(b)" :computing-for-s="waited" />
          <LearningEconomics v-else-if="b.id === 'economics'" :block="b" />
          <LearningMemory v-else-if="b.id === 'memory'" :block="b" />
          <LearningLessons v-else-if="b.id === 'lessons'" :block="b" :computing-for-s="waited" />
          <LearningVersions v-else-if="b.id === 'versions'" :block="b" :computing-for-s="waited" />
          <LearningHyps v-else-if="b.id === 'hyps'" :block="b" :computing-for-s="waited" />
          <section v-if="!KNOWN.has(b.id)" class="unk" :aria-label="b.title">
            <h2 class="unk-h">{{ b.title }}</h2>
            <p class="unk-t" role="status">{{ plainMessage(b) || LEARN_REPORT_FAILED }}</p>
          </section>
        </div>

        <div v-if="stages.length" class="blk" :style="{ '--i': Math.min(main.length, 5) }">
          <Stages :blocks="stages" />
        </div>
      </div>

      <p v-else-if="state === 'failed'" key="failed" class="note" role="status">{{ LEARN_REPORT_FAILED }}. Выберите другой режим в шапке или зайдите позже.</p>
      <p v-else-if="state === 'link'" key="link" class="note" role="status">{{ linkText }}</p>

      <div v-else key="load" class="stack skel" role="status">
        <span class="sr">{{ LEARN_LOADING }}</span>
        <div class="sk sk-v" />
        <div class="sk sk-b" />
        <div class="sk sk-b" />
      </div>
    </Transition>
  </main>
</template>

<style scoped>
.stack { display: grid; gap: var(--space-4); min-width: 0; }
.sr { position: absolute; width: 1px; height: 1px; margin: -1px; padding: 0; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; border: 0; }

.meta { margin: 0; display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-1) var(--space-3); font-size: 12px; line-height: 1.5; color: var(--muted); }
.age { font-variant-numeric: tabular-nums; }

.note { margin: 0; padding: var(--space-5) var(--space-2); font-size: 14px; line-height: 1.5; color: var(--muted); max-width: 70ch; text-wrap: pretty; }

.unk { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: var(--space-4); display: grid; gap: var(--space-2); }
.unk-h { margin: 0; font-size: 14px; font-weight: 600; }
.unk-t { margin: 0; font-size: 13px; color: var(--muted); }

/* скелет под размеры будущих блоков: вёрстка не прыгает при появлении данных */
.sk { border-radius: var(--radius); background: var(--panel); border: 1px solid var(--line); }
.sk-v { height: 168px; }
.sk-b { height: 240px; }

/* смена режима и первое появление: прозрачность на контейнере, блоки выезжают на 4 px лесенкой по 40 мс (вердикт имеет собственное появление) */
.fade-enter-active { transition: opacity var(--dur-enter) var(--ease-out); }
.fade-leave-active { transition: opacity 80ms linear; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
.blk { min-width: 0; transition: opacity var(--dur-enter) var(--ease-out), transform var(--dur-enter) var(--ease-out); transition-delay: calc(var(--i, 0) * 40ms); }
@starting-style {
  .blk { opacity: 0; transform: translateY(4px); }
}
@media (prefers-reduced-motion: no-preference) {
  .skel .sk { animation: sk-pulse 1.4s var(--ease-in-out) infinite alternate; }
}
@keyframes sk-pulse {
  from { opacity: 1; }
  to { opacity: 0.55; }
}
@media (prefers-reduced-motion: reduce) {
  .blk { transition: opacity var(--dur-enter) ease; transition-delay: 0ms; }
  @starting-style {
    .blk { transform: none; }
  }
}
@media (max-width: 520px) {
  .sk-v { height: 220px; }
  .sk-b { height: 200px; }
}
</style>
