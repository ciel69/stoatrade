<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { COMPUTING_GIVE_UP_S, blockView, type LearningBlock, type MemoryBlock } from '@/entities/learning'
import { UiCard } from '@/shared/ui'
import { MEM_COVER_HINT, MEM_COVER_LABEL, MEM_DIFF_HINT, MEM_DIFF_LABEL, MEM_HINT, MEM_HISTORY, memoryView } from '../lib/memory'

/**
 * Блок «Память против базы»: предсказывает ли память лучше базовой частоты (Brier памяти и базы, разность с интервалом, покрытие).
 * Тяжёлый расчёт идёт на сервере в фоне: пока блок «Считается…», показан скелет, готовый результат приходит повторным сообщением и заменяет его.
 * Один отложенный таймер нужен только на отказ ждать (сервер ждёт до 30 с); запросов он не шлёт.
 */
const props = defineProps<{ block: LearningBlock }>()

const gaveUp = ref(false)
let timer: ReturnType<typeof setTimeout> | null = null
const clear = (): void => {
  if (timer) clearTimeout(timer)
  timer = null
}
watch(() => props.block.status, (s) => {
  clear()
  gaveUp.value = false
  if (s === 'computing') timer = setTimeout(() => { gaveUp.value = true }, (COMPUTING_GIVE_UP_S + 1) * 1000)
}, { immediate: true })
onBeforeUnmount(clear)

const view = computed(() => blockView(props.block, gaveUp.value ? COMPUTING_GIVE_UP_S + 1 : 0))
const data = computed(() => (view.value.kind === 'data' ? memoryView(props.block as MemoryBlock) : null))
const waiting = computed(() => view.value.kind === 'computing')
</script>

<template>
  <UiCard :title="block.title" class="mem">
    <div class="body">
      <Transition name="mem-fade" mode="out-in" appear>
        <div v-if="data" key="data" class="in">
          <p class="hint">{{ MEM_HINT }}</p>

          <ul class="bars" aria-label="Brier памяти и базы">
            <li v-for="b in data.bars" :key="b.key" class="bar">
              <span class="bl">{{ b.label }}</span>
              <span class="track" aria-hidden="true"><i :class="b.key" :style="{ width: b.ratio * 100 + '%' }" /></span>
              <span class="bv">{{ b.text }}</span>
            </li>
          </ul>

          <p class="verdict" :class="data.verdict">{{ data.verdictText }}</p>

          <dl class="rows">
            <div class="row">
              <dt>{{ MEM_DIFF_LABEL }}<span class="sub">{{ MEM_DIFF_HINT }}</span></dt>
              <dd>
                <span class="val">{{ data.diff.value }}</span>
                <span v-if="data.diff.interval" class="sub">{{ data.diff.interval }}<template v-if="data.diff.level">, {{ data.diff.level }}</template></span>
                <span class="sub">{{ data.diff.n }}<template v-if="data.small">, {{ data.small }}</template></span>
              </dd>
            </div>
            <div class="row">
              <dt>{{ MEM_COVER_LABEL }}<span class="sub">{{ MEM_COVER_HINT }}</span></dt>
              <dd>
                <span class="val">{{ data.cover.value }}</span>
                <span v-if="data.cover.interval" class="sub">{{ data.cover.interval }}</span>
                <span class="sub">{{ data.cover.n }}<template v-if="data.cover.small">, {{ data.cover.small }}</template></span>
              </dd>
            </div>
            <div class="row">
              <dt>Доля верных прогнозов</dt>
              <dd>
                <span class="val">{{ data.accuracy }}</span>
                <span class="sub">{{ data.evaluated }}</span>
              </dd>
            </div>
            <div class="row hist">
              <dt>{{ MEM_HISTORY }}</dt>
              <dd><span class="stub">{{ data.history }}</span></dd>
            </div>
          </dl>
        </div>

        <div v-else-if="waiting" key="wait" class="in wait" role="status" aria-live="polite">
          <p class="state">{{ view.message }}</p>
          <div class="sk" aria-hidden="true">
            <span class="sk-line w60" /><span class="sk-line w45" /><span class="sk-line w70" /><span class="sk-line w35" />
          </div>
        </div>

        <p v-else :key="view.kind" class="state" role="status">{{ view.message }}</p>
      </Transition>
    </div>
  </UiCard>
</template>

<style scoped>
.mem { min-width: 0; }
.body { min-height: 168px; }
.in { display: grid; gap: var(--space-3); min-width: 0; }
.hint { margin: 0; font-size: 12px; line-height: 1.5; color: var(--muted); }
.state { margin: 0; font-size: 13px; color: var(--muted); }
.wait .state { color: var(--text); }
.sub { display: block; font-size: 11px; line-height: 1.4; color: var(--muted); font-variant-numeric: tabular-nums; }

.bars { list-style: none; margin: 0; padding: 0; display: grid; gap: var(--space-2); }
.bar { display: grid; grid-template-columns: 52px minmax(0, 1fr) 48px; gap: var(--space-3); align-items: center; font-size: 13px; }
.bl { color: var(--muted); }
.track { height: 8px; border-radius: 99px; background: var(--line); overflow: hidden; }
.track i { display: block; height: 100%; border-radius: 99px; }
.track i.mem { background: var(--accent); }
.track i.base { background: var(--muted); }
.bv { text-align: right; font-weight: 600; font-variant-numeric: tabular-nums; }

.verdict { margin: 0; font-size: 13px; line-height: 1.5; color: var(--text); font-weight: 600; }
.verdict.few, .verdict.none, .verdict.no_interval { color: var(--muted); font-weight: 400; }

.rows { margin: 0; display: grid; }
.row { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.1fr); gap: var(--space-3); padding: var(--space-2) 0; border-top: 1px solid var(--line); font-size: 12px; }
.row dt { color: var(--text); min-width: 0; }
.row dd { margin: 0; min-width: 0; text-align: right; }
.val { display: block; font-size: 14px; font-weight: 700; font-variant-numeric: tabular-nums; }
.hist dt, .stub { color: var(--muted); }
.stub { font-size: 12px; }

.sk { display: grid; gap: var(--space-3); }
.sk-line { display: block; height: 10px; border-radius: 99px; background: var(--line); animation: mem-pulse 1.4s ease-in-out infinite; }
.sk-line:nth-child(2) { animation-delay: 120ms; }
.sk-line:nth-child(3) { animation-delay: 240ms; }
.sk-line:nth-child(4) { animation-delay: 360ms; }
.w35 { width: 35%; } .w45 { width: 45%; } .w60 { width: 60%; } .w70 { width: 70%; }
@keyframes mem-pulse { 0%, 100% { opacity: 0.45; } 50% { opacity: 1; } }

/* смена состояния («Считается…» -> данные): только opacity и сдвиг на 4 px */
.mem-fade-enter-active { transition: opacity var(--dur-enter) var(--ease-out), transform var(--dur-enter) var(--ease-out); }
.mem-fade-leave-active { transition: opacity var(--dur-fast) var(--ease-out); }
.mem-fade-enter-from { opacity: 0; transform: translateY(4px); }
.mem-fade-leave-to { opacity: 0; }

@media (prefers-reduced-motion: reduce) {
  .mem-fade-enter-from { transform: none; }
  .mem-fade-enter-active { transition: opacity var(--dur-enter) ease; }
  .sk-line { animation: none; opacity: 0.7; }
}
</style>
