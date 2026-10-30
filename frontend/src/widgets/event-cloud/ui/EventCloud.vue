<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { describeEvent, useEventStore } from '@/entities/event'

const events = useEventStore()
const open = ref(false)
const panel = ref<HTMLElement | null>(null)
const button = ref<HTMLButtonElement | null>(null)

onMounted(() => events.start())

const badge = computed(() => (events.unread > 99 ? '99+' : String(events.unread)))
const rows = computed(() => events.items.map((e) => ({ e, text: describeEvent(e), time: e.ts.slice(11, 19) })))

function toggle(): void {
  open.value = !open.value
}
function close(): void {
  open.value = false
  button.value?.focus()
}
function onKey(ev: KeyboardEvent): void {
  if (ev.key === 'Escape' && open.value) close()
}
onMounted(() => window.addEventListener('keydown', onKey))
onUnmounted(() => window.removeEventListener('keydown', onKey))

watch(open, async (v) => {
  if (v) {
    events.markSeen()
    await nextTick()
    panel.value?.focus()
  }
})
// пока панель открыта, всё приходящее сразу считается прочитанным
watch(() => events.lastSeq, () => open.value && events.markSeen())
</script>

<template>
  <div class="cloud">
    <Transition name="panel">
      <aside v-if="open" id="event-panel" ref="panel" class="panel" tabindex="-1" aria-label="События">
        <header class="head">
          <span>События</span>
          <button type="button" class="x" aria-label="Закрыть события" @click="close">×</button>
        </header>
        <ul v-if="rows.length" class="list">
          <li v-for="r in rows" :key="r.e.seq" class="item" :class="`t-${r.text.tone}`">
            <span class="time">{{ r.time }}</span>
            <span class="what"><b v-if="r.e.sym">{{ r.e.sym }} </b>{{ r.text.title }}<span v-if="r.text.detail" class="detail"> · {{ r.text.detail }}</span></span>
          </li>
        </ul>
        <p v-else class="empty">Событий пока нет.</p>
      </aside>
    </Transition>

    <button ref="button" type="button" class="fab" :aria-expanded="open" aria-controls="event-panel" :aria-label="`События, новых: ${events.unread}`" @click="toggle">
      <svg viewBox="0 0 64 64" width="26" height="26" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M20 46H46C54 46 58 39 56 32C54 26 48 24 44 26C42 19 36 15 30 17C24 19 22 24 22 27C14 27 10 33 12 39C13 43 16 46 20 46Z" />
      </svg>
      <span v-if="events.unread" class="badge">{{ badge }}</span>
    </button>
  </div>
</template>

<style scoped>
.cloud { position: fixed; right: var(--space-5); bottom: var(--space-5); z-index: 20; display: flex; flex-direction: column; align-items: flex-end; gap: var(--space-3); }
.fab {
  position: relative; width: 48px; height: 48px; display: grid; place-items: center;
  background: var(--panel); color: var(--muted); border: 1px solid var(--line2); border-radius: 50%; cursor: pointer;
  transition: color var(--dur-fast) ease, border-color var(--dur-fast) ease, transform 120ms var(--ease-out);
}
.fab[aria-expanded="true"] { color: var(--accent); border-color: var(--accent); }
.fab:active { transform: scale(0.96); }
@media (hover: hover) and (pointer: fine) {
  .fab:hover { color: var(--text); border-color: var(--muted); }
}
.badge { position: absolute; top: -5px; right: -5px; min-width: 20px; height: 20px; padding: 0 5px; display: grid; place-items: center; border-radius: 99px; background: var(--accent); color: var(--bg); font-size: 11px; font-weight: 700; font-variant-numeric: tabular-nums; }
.panel { width: min(380px, calc(100vw - 2 * var(--space-5))); max-height: min(60vh, 520px); display: flex; flex-direction: column; background: var(--panel); border: 1px solid var(--line2); border-radius: var(--radius); box-shadow: 0 8px 24px rgba(0, 0, 0, 0.28); outline: none; transform-origin: bottom right; }
.head { display: flex; align-items: center; justify-content: space-between; padding: var(--space-3) var(--space-4); border-bottom: 1px solid var(--line); font-weight: 600; font-size: 13px; }
.x { background: transparent; border: 0; color: var(--muted); font-size: 20px; line-height: 1; cursor: pointer; padding: 0 var(--space-1); }
.list { list-style: none; margin: 0; padding: var(--space-1) 0; overflow-y: auto; }
.item { display: flex; gap: var(--space-3); padding: 6px var(--space-4); font-size: 12px; line-height: 1.35; }
.time { color: var(--muted); font-variant-numeric: tabular-nums; flex: none; }
.what { min-width: 0; overflow-wrap: anywhere; }
.detail { color: var(--muted); }
.t-bad .what { color: var(--neg); }
.t-good .what { color: var(--pos); }
.empty { margin: 0; padding: var(--space-5); text-align: center; color: var(--muted); font-size: 13px; }

/* открытие панели: редкое действие (занять место и вернуть его), поэтому короткий выезд; закрытие быстрее и без движения вверх */
.panel-enter-from { opacity: 0; transform: translateY(8px) scale(0.98); }
.panel-enter-active { transition: opacity var(--dur-enter) var(--ease-out), transform var(--dur-enter) var(--ease-out); }
.panel-leave-active { transition: opacity var(--dur-fast) var(--ease-out); }
.panel-leave-to { opacity: 0; }
@media (prefers-reduced-motion: reduce) {
  .panel-enter-from { transform: none; }
  .fab:active { transform: none; }
}
</style>
