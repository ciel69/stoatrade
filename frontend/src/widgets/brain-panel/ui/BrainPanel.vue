<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ThoughtBubble, pickThoughts, useBrainStore } from '@/entities/brain'
import { UiBrainIcon, type BrainIconState } from '@/shared/ui'
import { ageText, usd } from '@/shared/lib'

const brain = useBrainStore()
const now = ref(Date.now() / 1000)
const paused = ref(false)                       // пока курсор или фокус на облаке, время для мыслей стоит: можно дочитать, ничего не гаснет
let timer: ReturnType<typeof setInterval> | null = null
onMounted(() => {
  brain.start()
  timer = setInterval(() => {
    if (!paused.value) now.value = Date.now() / 1000
  }, 1000)
})
onUnmounted(() => timer && clearInterval(timer))

const snap = computed(() => brain.snapshot)
const loaded = computed(() => snap.value !== null)
const icon = computed<BrainIconState>(() => (loaded.value ? brain.status : 'idle'))        // до первого снимка не показываем «не работает»
const statusText = computed(() => (loaded.value ? brain.statusText : 'подключение…'))
const thoughts = computed(() => pickThoughts(brain.decisions, now.value))
const active = computed(() => snap.value?.active ?? null)
const spend = computed(() => brain.roles.reduce((s, r) => s + (r.today?.usd ?? 0), 0))
const calls = computed(() => brain.roles.reduce((s, r) => s + (r.today?.calls ?? 0), 0))
const fell = computed(() => brain.roles.filter((r) => r.last?.fallback_from).length)       // отвечал запасной профиль (сервер помечает fallback_from)
</script>

<template>
  <section class="panel" aria-label="Мозг">
    <header class="top">
      <UiBrainIcon :state="icon" :size="56" :label="`мозг: ${statusText}`" />
      <div class="who">
        <div class="model">{{ active?.profile ?? 'модель не отвечала' }}</div>
        <div class="sub">
          <span class="state" :class="loaded ? `st-${brain.status}` : ''">{{ statusText }}</span>
          <template v-if="active"> · {{ active.provider }} · {{ active.role }} · {{ ageText(active.t, now) }}</template>
        </div>
      </div>
    </header>

    <p v-if="snap?.protect_reason" class="alert">{{ snap.protect_reason }}</p>
    <p v-else-if="fell" class="alert soft">отвечает запасная модель у ролей: {{ fell }}</p>

    <!-- область под мысли фиксированной высоты: новые не раздвигают панель, лишние прокручиваются -->
    <div class="sky" @pointerenter="paused = true" @pointerleave="paused = false" @focusin="paused = true" @focusout="paused = false">
      <TransitionGroup name="feed" tag="ul" class="cloud" aria-live="polite">
        <ThoughtBubble v-for="t in thoughts" :key="t.row.seq" :thought="t" :now="now" />
      </TransitionGroup>
      <p v-if="!thoughts.length" class="quiet">{{ !loaded ? '' : brain.status === 'down' ? 'Мозг не отвечает.' : 'Тихо: новых мыслей нет.' }}</p>
    </div>

    <footer class="foot">
      очередь {{ snap?.queue?.new ?? 0 }} · вызовов сегодня {{ calls }} · {{ usd(spend) }}
    </footer>
  </section>
</template>

<style scoped>
.panel { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: var(--space-4); display: flex; flex-direction: column; gap: var(--space-3); min-width: 0; }
.top { display: flex; align-items: center; gap: var(--space-4); }
.who { min-width: 0; }
.model { font-size: 20px; font-weight: 700; letter-spacing: -0.02em; line-height: 1.15; overflow-wrap: anywhere; }
.sub { font-size: 12px; color: var(--muted); margin-top: 2px; }
.state { font-weight: 600; }
.st-thinking { color: var(--zone-brain); }
.st-protect { color: var(--warn); }
.st-down { color: var(--neg); }
.st-idle { color: var(--pos); }
.alert { margin: 0; padding: var(--space-2) var(--space-3); border: 1px solid var(--warn); border-radius: var(--radius-sm); color: var(--warn); font-size: 13px; }
.alert.soft { border-color: var(--line2); color: var(--muted); }
.sky { --thought-h: 76px; position: relative; height: calc(var(--thought-h) * 3 + var(--space-2) * 2); overflow-y: auto; scrollbar-gutter: stable; }
.cloud { list-style: none; margin: 0; padding: 0; display: grid; gap: var(--space-2); position: relative; }
.quiet { position: absolute; inset: 0; margin: 0; display: grid; place-items: center; color: var(--muted); font-size: 13px; }
.foot { margin-top: auto; padding-top: var(--space-2); border-top: 1px solid var(--line); font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
</style>
