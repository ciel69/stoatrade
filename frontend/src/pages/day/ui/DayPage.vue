<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useActionStore } from '@/entities/action'
import { DayReportPanel, isDayMock, useDayStore } from '@/entities/day'
import { DayCourse } from '@/widgets/day/course'
import { DayHero } from '@/widgets/day/hero'
import { DayList } from '@/widgets/day/list'
import { DayNeeds } from '@/widgets/day/needs'
import { useLive } from '@/shared/api'
import { UiSidePanel } from '@/shared/ui'

/**
 * Страница «Торговля · День»: одно предложение о том, что происходит и что нужно от вас, затем «Нужно ваше решение» (перенос позиции через сутки, возобновить день, итог готов),
 * ход дня (шаги запуска и закрытия, причины сбоев, перенесённые позиции, чек-лист) и список дней. Отчёт дня открывается в боковой панели, выбор хранится в адресе (?day=ID).
 * Данные приходят по WebSocket темой day (отчёт: операция day_report). Отладка без сервера: ?daymock=mock (идёт закрытие, есть вопрос о переносе), mock-resume (нужно возобновить), mock-done (итог готов),
 * mock-abort (старт отклонён), mock-empty (дней не было). Реальных команд в этих режимах не отправляется.
 */
const store = useDayStore()
const action = useActionStore()
const route = useRoute()
const router = useRouter()
onMounted(() => {
  store.start()
  if (isDayMock(new URLSearchParams(location.search).get('daymock'))) action.mockDay()
})

const selected = computed(() => (typeof route.query.day === 'string' && route.query.day ? route.query.day : null))
const reply = computed(() => (selected.value ? store.reports[selected.value] : undefined))
const { link } = useLive()
// отчёт запрашивается при выборе дня и повторно, когда связь появилась позже (адрес открыт до подключения сокета)
watch([selected, link], ([id, l]) => { if (id && (l === 'open' || store.mocked)) store.requestReport(id) }, { immediate: true })

function pick(id: string): void {
  void router.replace({ query: { ...route.query, day: selected.value === id ? undefined : id } })
}
function close(): void {
  void router.replace({ query: { ...route.query, day: undefined } })
}
</script>

<template>
  <main class="page">
    <DayHero />
    <DayNeeds @open-report="pick" />
    <div class="cols">
      <DayCourse />
      <DayList :selected="selected" @select="pick" />
    </div>
    <UiSidePanel :open="!!selected" label="Итог дня" @close="close">
      <DayReportPanel v-if="selected" :id="selected" :reply="reply" @close="close" />
    </UiSidePanel>
  </main>
</template>

<style scoped>
.cols { display: grid; grid-template-columns: minmax(0, 1fr) minmax(280px, 380px); gap: var(--space-4); align-items: start; }
.cols > * { min-width: 0; }
@media (max-width: 1000px) {
  .cols { grid-template-columns: minmax(0, 1fr); }
}
</style>
