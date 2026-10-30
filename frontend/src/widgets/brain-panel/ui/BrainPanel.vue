<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { BrainStatusBadge, DecisionItem, useBrainStore } from '@/entities/brain'
import { UiCard, UiChip } from '@/shared/ui'
import { ageText, usd } from '@/shared/lib'

const brain = useBrainStore()
const now = ref(Date.now() / 1000)
let timer: ReturnType<typeof setInterval> | null = null
onMounted(() => {
  brain.start()
  timer = setInterval(() => (now.value = Date.now() / 1000), 1000)      // возраст «N с назад» считает клиент: сервер данные не гоняет
})
onUnmounted(() => timer && clearInterval(timer))

const snap = computed(() => brain.snapshot)
const active = computed(() => snap.value?.active ?? null)
const queued = computed(() => snap.value?.queue?.new ?? 0)
const spendToday = computed(() => brain.roles.reduce((s, r) => s + (r.today?.usd ?? 0), 0))
const callsToday = computed(() => brain.roles.reduce((s, r) => s + (r.today?.calls ?? 0), 0))
</script>

<template>
  <UiCard title="Мозг" sub="решения, пояснения и активная модель">
    <template #head>
      <BrainStatusBadge :status="brain.status" :text="brain.statusText" />
    </template>

    <p v-if="snap?.protect_reason" class="warn">Только защита: {{ snap.protect_reason }}</p>

    <dl class="facts">
      <div>
        <dt>Активная модель</dt>
        <dd v-if="active">
          <b>{{ active.profile }}</b>
          <span class="muted"> · {{ active.provider }} · {{ active.role }} · {{ ageText(active.t, now) }}</span>
        </dd>
        <dd v-else class="muted">нет вызовов за последние 10 минут</dd>
      </div>
      <div>
        <dt>Очередь</dt>
        <dd>{{ queued }} <span class="muted">· в работе {{ snap?.inflight ?? 0 }}</span></dd>
      </div>
      <div>
        <dt>Сегодня</dt>
        <dd>{{ callsToday }} <span class="muted">вызовов · {{ usd(spendToday) }}</span></dd>
      </div>
    </dl>

    <ul v-if="brain.roles.length" class="roles">
      <li v-for="r in brain.roles" :key="r.role">
        <span class="role">{{ r.role }}</span>
        <UiChip tone="accent">{{ r.primary?.id ?? '–' }}</UiChip>
        <UiChip v-if="r.last && r.last.profile !== r.primary?.id" tone="warn">отвечал {{ r.last.profile }}</UiChip>
        <UiChip v-if="r.last && r.last.ok === false" tone="bad">ошибка {{ r.last.err ?? '' }}</UiChip>
      </li>
    </ul>

    <h3 class="feed-title">Последние решения</h3>
    <TransitionGroup v-if="brain.decisions.length" name="feed" tag="ul" class="feed">
      <DecisionItem v-for="d in brain.decisions" :key="d.seq" :row="d" :now="now" />
    </TransitionGroup>
    <p v-else class="muted empty">Решений пока нет: мозг ждёт событий.</p>
  </UiCard>
</template>

<style scoped>
.warn { color: var(--warn); margin: 0 0 var(--space-3); font-size: 13px; }
.facts { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: var(--space-3); margin: 0 0 var(--space-4); }
.facts dt { font-size: 12px; color: var(--muted); }
.facts dd { margin: 2px 0 0; font-size: 15px; }
.muted { color: var(--muted); font-size: 13px; }
.roles { list-style: none; margin: 0 0 var(--space-4); padding: 0; display: grid; gap: var(--space-2); }
.roles li { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-2); }
.role { min-width: 150px; font-size: 13px; }
.feed-title { margin: 0 0 var(--space-1); font-size: 13px; color: var(--muted); font-weight: 600; }
.feed { list-style: none; margin: 0; padding: 0; position: relative; }
.empty { margin: 0; }
</style>
