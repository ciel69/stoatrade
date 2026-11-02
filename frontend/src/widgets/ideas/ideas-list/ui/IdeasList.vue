<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { IdeaCard, IdeaResultRow, ideasOf, useIdeaStore, type IdeaTab } from '@/entities/idea'
import { useNow } from '@/shared/lib'

/**
 * Идеи одной вкладки: «Ждут уровня» карточками с полосой до уровня, «Стали планом» и «Не сработали» строками с исходом. Подробности выбранной идеи показывает страница в боковой панели, список по 10 с кнопкой «Показать ещё».
 */
const props = defineProps<{ tab: Exclude<IdeaTab, 'auto'>; selected: string | null }>()
const emit = defineEmits<{ select: [id: string] }>()
const store = useIdeaStore()
const now = useNow()
const STEP = 20
const limit = ref(10)
watch(() => props.tab, () => { limit.value = 10 })

const rows = computed(() => (store.snapshot ? ideasOf(store.snapshot, props.tab) : []))
const shown = computed(() => rows.value.slice(0, limit.value))
const EMPTY: Record<Exclude<IdeaTab, 'auto'>, string> = {
  active: 'Идея появляется, когда мозг видит уровень, но входить рано. Когда цена дойдёт до уровня, он проверит вход и, если всё сходится, создаст план.',
  taken: 'Появится, когда по идее будет подан план.',
  skipped: 'Появится, когда идея истечёт или не сработает.',
}
</script>

<template>
  <section class="ideas" :aria-label="tab">
    <template v-if="store.snapshot">
      <ul v-if="rows.length" class="list">
        <template v-for="i in shown" :key="i.id">
          <IdeaCard v-if="tab === 'active'" :idea="i" :now="now" :selected="selected === i.id" @select="emit('select', i.id)" />
          <IdeaResultRow v-else :idea="i" :now="now" :selected="selected === i.id" @select="emit('select', i.id)" />
        </template>
      </ul>
      <p v-else class="empty">{{ EMPTY[tab] }}</p>
      <button v-if="rows.length > limit" type="button" class="more" @click="limit += STEP">Показать ещё {{ Math.min(STEP, rows.length - limit) }}</button>
    </template>
    <div v-else class="skel" aria-busy="true" />
  </section>
</template>

<style scoped>
.ideas { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: var(--space-4); min-width: 0; }
/* сетка по 440 px; разделитель снизу, нижняя линия последнего ряда срезана clip-path */
.list { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(min(460px, 100%), 1fr)); column-gap: var(--space-6); clip-path: inset(0 calc(var(--space-3) * -1) 1px calc(var(--space-3) * -1)); }
.list > :deep(li) { border-top: 0; border-bottom: 1px solid var(--line); }
.list > :deep(li:first-child) { padding-top: var(--space-3); }
/* карточка кликабельна: поля внутрь, чтобы выделение не упиралось в текст (как строки плана) */
.list > :deep(li.card) { cursor: pointer; padding-inline: var(--space-3); margin-inline: calc(var(--space-3) * -1); border-radius: var(--radius-sm); transition: background-color var(--dur-fast) ease; }
.list > :deep(li.card.sel) { background: color-mix(in srgb, var(--accent) 9%, var(--panel)); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent) 35%, transparent); }
@media (hover: hover) and (pointer: fine) {
  .list > :deep(li.card:not(.sel):hover) { background: var(--panel2); }
}
.empty { margin: 0; font-size: 13px; color: var(--muted); max-width: 60ch; }
.skel { height: 120px; border-radius: var(--radius-sm); background: var(--panel2); }
.more { margin-top: var(--space-3); padding: 6px 14px; border: 1px solid var(--line2); border-radius: var(--radius-sm); background: var(--panel2); color: var(--text); font: inherit; font-size: 12px; cursor: pointer; transition: border-color var(--dur-fast) ease; }
.more:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
@media (hover: hover) and (pointer: fine) {
  .more:hover { border-color: var(--accent); }
}
</style>
