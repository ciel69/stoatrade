<script setup lang="ts">
import { computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { DEFAULT_MIN_N, ExpGroupPanel, rowViews, useExperimentStore } from '@/entities/experiment'
import { useTradingStore } from '@/entities/trading'
import { ExpIntro } from '@/widgets/experiments/intro'
import { ExpTable } from '@/widgets/experiments/table'
import { useLive } from '@/shared/api'
import { isExpCut, type ExpCut } from '@/shared/lib'
import { UiSidePanel } from '@/shared/ui'

/**
 * Страница «Торговля · Эксперименты»: чьи гипотезы подтверждены, как это понимать и результаты закрытых сделок по выбранному разрезу (гипотезы, эксперименты, варианты, схема входа, тип сессии, монета и т.д.).
 * Режим (бумага, реальные деньги, тестовая биржа) берётся из переключателя в шапке. Выбор хранится в адресе: ?by=РАЗРЕЗ и ?group=РЕЖИМ:ИМЯ (боковая панель со сделками группы).
 * Данные приходят по WebSocket: запрос отправляется при заходе, смене разреза или режима и когда закрылась новая сделка. Отладка без сервера: ?exp=mock, ?exp=mock-confirmed, ?exp=mock-empty.
 */
const store = useExperimentStore()
const trading = useTradingStore()
const route = useRoute()
const router = useRouter()
const { link } = useLive()
store.start()                // до первого запроса: образец ?exp=mock должен быть известен

const by = computed<ExpCut>(() => (isExpCut(route.query.by) ? route.query.by : 'hyp'))
const selected = computed(() => (typeof route.query.group === 'string' && route.query.group ? route.query.group : null))

// запрос уходит, когда сокет открыт; смена разреза или режима и новая закрытая сделка повторяют его (опроса по таймеру нет)
watch(
  [by, () => trading.mode, link, () => trading.trades?.total],
  ([b, m, l]) => { if (l === 'open' || store.mocked) store.request(b, m) },
  { immediate: true, flush: 'post' },
)

const view = computed(() => (selected.value ? (rowViews(store.detail, by.value).find((r) => r.key === selected.value) ?? null) : null))

function setBy(v: ExpCut): void {
  void router.replace({ query: { ...route.query, by: v === 'hyp' ? undefined : v, group: undefined } })
}
function pick(key: string): void {
  void router.replace({ query: { ...route.query, group: selected.value === key ? undefined : key } })
}
function close(): void {
  void router.replace({ query: { ...route.query, group: undefined } })
}
</script>

<template>
  <main class="page">
    <ExpIntro />
    <ExpTable :by="by" :selected="selected" @update:by="setBy" @select="pick" />
    <UiSidePanel :open="!!view" label="Группа сделок" @close="close">
      <ExpGroupPanel v-if="view" :key="view.key" :cut="by" :view="view" :min-n="store.detail?.min_n ?? DEFAULT_MIN_N" @close="close" />
    </UiSidePanel>
  </main>
</template>
