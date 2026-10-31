<script setup lang="ts">
import { computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useBriefStore } from '@/entities/brief'
import { useIdeaStore } from '@/entities/idea'
import { useTradingStore } from '@/entities/trading'
import { BriefList } from '@/widgets/brief/brief-list'
import { BriefView } from '@/widgets/brief/brief-view'
import { useLive } from '@/shared/api'

/**
 * Страница «Досье»: слева лента сохранённых досье, справа содержимое выбранного с оглавлением. Выбор хранится в адресе (?id=B-…); без него показывается самое новое (и следует за новыми).
 * Досье запрашивается по WebSocket (op brief) при смене выбора и при переподключении; ссылки на досье ведут решения (brief_id).
 */
const route = useRoute()
const router = useRouter()
const store = useBriefStore()

const picked = computed(() => (typeof route.query.id === 'string' && route.query.id ? route.query.id : null))
const id = computed(() => picked.value ?? store.list?.items[0]?.id ?? null)
const reply = computed(() => (id.value ? store.detail[id.value] ?? null : null))

function ask(): void {
  if (id.value) store.request(id.value)
}
let off: (() => void) | undefined
onMounted(() => {
  store.start()
  useIdeaStore().start()
  useTradingStore().start()
  off = useLive().socket.onState((s) => { if (s === 'open') ask() })
})
onUnmounted(() => off?.())
watch(id, ask)

function pick(next: string): void {
  void router.replace({ query: next === store.list?.items[0]?.id && !picked.value ? {} : { id: next } })
}
</script>

<template>
  <main class="page">
    <aside class="side"><BriefList :selected="id" @select="pick" /></aside>
    <div class="body">
      <BriefView v-if="reply?.ok && reply.data && id" :key="id" :brief="reply.data" :id="id" />
      <p v-else-if="reply && !reply.ok" class="note">Досье {{ id }} не найдено ({{ reply.error }}). Файлы хранятся 14 дней.</p>
      <p v-else-if="store.list && !store.list.items.length" class="note">Сохранённых досье пока нет: команда <code>./tb brief</code> сохраняет каждый вызов.</p>
      <div v-else class="skel" aria-busy="true" />
    </div>
  </main>
</template>

<style scoped>
.page { max-width: 1480px; margin: 0 auto; padding: var(--space-5); display: grid; grid-template-columns: 250px minmax(0, 1fr); gap: var(--space-4); align-items: start; }
.side { position: sticky; top: var(--space-4); min-width: 0; }
.body { min-width: 0; }
.note { margin: 0; padding: var(--space-5); background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); font-size: 13px; color: var(--muted); }
.skel { height: 320px; border-radius: var(--radius); background: var(--panel); border: 1px solid var(--line); }
@media (max-width: 900px) {
  .page { grid-template-columns: minmax(0, 1fr); }
  .side { position: static; }
  .side :deep(.list) { max-height: 220px; }
}
</style>
