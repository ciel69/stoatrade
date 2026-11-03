<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'
import { useActionStore } from '@/entities/action'
import {
  DISABLE_ALL_TITLE, DISABLE_ALL_WHAT, DISABLE_WHAT, EMERGENCY_TEXT, MemorySafe, blockState, disableTitle, useMemoryStore, type LessonRow, type MemoryOp, type RuleBrief,
} from '@/entities/memory'
import { MemoryCases } from '@/widgets/memory/cases'
import { MemoryLessons } from '@/widgets/memory/lessons'
import { MemoryPath } from '@/widgets/memory/path'
import { MemoryRules } from '@/widgets/memory/rules'
import { useLive } from '@/shared/api'
import { useNow } from '@/shared/lib'

/**
 * Вкладка «Мозг · Память» (R2.5): что помнит система и как наблюдение доходит до правила. Только чтение плюс аварийные действия владельца,
 * необязательные для работы платформы: отклонить урок (без диалога), отключить правило и «Отключить все правила» (единое окно «Принимаю риски»).
 * Четыре блока запрашиваются независимо (заход, возвращение связи, после действия); сбой одного блока остальные не трогает.
 */
const store = useMemoryStore()
const action = useActionStore()
const { link } = useLive()
const tick = useNow()
const now = computed(() => Math.floor(tick.value / 60) * 60)        // сроки в минутах: строки не перерисовываются каждую секунду

store.start()
action.start()
onMounted(() => { if (link.value === 'open') store.requestAll() })
watch(link, (s, prev) => { if (s === 'open' && prev !== 'open') store.requestAll() })

const open = computed(() => link.value === 'open')
const st = (op: MemoryOp) => blockState(store.details[op], store.pending[op], open.value)
const loading = computed(() => (['memory_cases', 'memory_lessons', 'memory_rules', 'memory_path'] as MemoryOp[]).some((op) => st(op) === 'loading'))

const rules = computed(() => store.details.memory_rules)
const version = computed(() => rules.value?.current ?? rules.value?.mode?.ver ?? null)
const busy = computed(() => action.active)

const AFTER: MemoryOp[] = ['memory_rules', 'memory_lessons', 'memory_path']
const refresh = (): void => store.requestAll(AFTER)

function disableRule(r: RuleBrief): void {
  if (!r.rule_id) return
  action.run({ channel: 'memory', action: 'rule_disable', args: { rule_id: r.rule_id }, title: disableTitle(r.rule_id), what: DISABLE_WHAT, onDone: refresh })
}
function disableAll(): void {
  action.run({ channel: 'memory', action: 'rules_disable_all', args: {}, title: DISABLE_ALL_TITLE, what: DISABLE_ALL_WHAT, onDone: refresh, onFail: refresh })
}
function reject(l: LessonRow): void {
  if (!l.lesson_id) return
  action.run({ channel: 'memory', action: 'lesson_reject', args: { lesson_id: l.lesson_id }, title: `Отклонить урок ${l.lesson_id}`, onDone: refresh })
}
const canAll = computed(() => action.canMemory('rules_disable_all'))
</script>

<template>
  <main class="page memory-page" :aria-busy="loading">
    <header class="top">
      <div class="lead">
        <h1 class="title">Память</h1>
        <p class="sum">Что система запомнила о своих решениях и каким правилам научилась.</p>
      </div>
      <div class="sos" role="group" aria-labelledby="sos-h">
        <p id="sos-h" class="sos-text">{{ EMERGENCY_TEXT }}</p>
        <button type="button" class="kill" :disabled="busy || !canAll" @click="disableAll">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M12 3v9m5.7-6.7a8 8 0 1 1-11.4 0" /></svg>
          {{ DISABLE_ALL_TITLE }}
        </button>
      </div>
    </header>

    <MemorySafe what="о пути уроков">
      <MemoryPath :detail="store.details.memory_path" :state="st('memory_path')" :now="now" :version="version" />
    </MemorySafe>

    <div class="grid">
      <div class="col">
        <MemorySafe what="о правилах">
          <MemoryRules :detail="rules" :state="st('memory_rules')" :busy="busy" @disable="disableRule" />
        </MemorySafe>
        <MemorySafe what="об уроках">
          <MemoryLessons :detail="store.details.memory_lessons" :state="st('memory_lessons')" :now="now" :busy="busy" @reject="reject" />
        </MemorySafe>
      </div>
      <div class="col">
        <MemorySafe what="о случаях">
          <MemoryCases :detail="store.details.memory_cases" :state="st('memory_cases')" />
        </MemorySafe>
      </div>
    </div>
  </main>
</template>

<style scoped>
.top { display: flex; flex-wrap: wrap; align-items: flex-end; justify-content: space-between; gap: var(--space-4); }
.lead { display: grid; gap: var(--space-1); min-width: 0; }
.title { margin: 0; font-size: 24px; font-weight: 700; line-height: 1.1; letter-spacing: -0.02em; }
.sum { margin: 0; font-size: 13px; color: var(--muted); }

.sos { display: flex; align-items: center; gap: var(--space-3); max-width: 560px; }
.sos-text { margin: 0; font-size: 12px; line-height: 1.45; color: var(--muted); text-wrap: pretty; }
.kill { flex: none; display: inline-flex; align-items: center; gap: var(--space-2); min-height: 40px; padding: 0 var(--space-4); border: 0; border-radius: 10px; background: var(--neg); color: var(--bg); font-size: 13px; font-weight: 650; cursor: pointer; transition: filter var(--dur-fast) ease, opacity var(--dur-fast) ease, transform 120ms var(--ease-out); }
.kill:disabled { opacity: 0.5; cursor: not-allowed; }
.kill:active:not(:disabled) { transform: scale(0.98); }
@media (hover: hover) and (pointer: fine) {
  .kill:not(:disabled):hover { filter: brightness(1.08); }
}

.grid { display: grid; grid-template-columns: minmax(0, 1.65fr) minmax(0, 1fr); gap: var(--space-4); align-items: start; }
.col { display: grid; gap: var(--space-4); min-width: 0; }

/* первое появление блоков: прозрачность, без движения содержимого с цифрами */
.col > *, .top + * { transition: opacity var(--dur-enter) var(--ease-out); }
@starting-style {
  .col > *, .top + * { opacity: 0; }
}

@media (max-width: 1100px) {
  .grid { grid-template-columns: minmax(0, 1fr); }
}
@media (max-width: 640px) {
  .sos { flex-direction: column; align-items: stretch; }
  .kill { justify-content: center; }
}
</style>
