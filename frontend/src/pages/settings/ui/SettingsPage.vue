<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute, useRouter, type LocationQueryRaw } from 'vue-router'
import { useActionStore } from '@/entities/action'
import { useRoleStore } from '@/entities/role'
import { parseUsagePanel, usagePanelQuery, type UsageBy, type UsagePeriod } from '@/entities/usage'
import { ExchangeBoard } from '@/widgets/settings/connect-panel'
import { ModeBoard } from '@/widgets/settings/mode-board'
import { ProfilesBoard } from '@/widgets/settings/profiles-board'
import { ProvidersBoard } from '@/widgets/settings/providers-board'
import { RolesBoard } from '@/widgets/settings/roles-board'
import { SwapsPanel } from '@/widgets/settings/swaps-panel'
import { UsageBoard } from '@/widgets/settings/usage-board'
import { UsagePanel } from '@/widgets/settings/usage-panel'
import { UiSidePanel } from '@/shared/ui'

/**
 * Страница «Система · Настройки»: три списка и три формы. «Кто думает» (роли), «Профили моделей», «Провайдеры»: у каждой строки кнопка «Изменить», у профилей и провайдеров «Добавить»;
 * и создание, и правка открывают одну и ту же форму в окне. Ниже «Биржа», «Сколько стоит» (расход мозга, история смен моделей) и «Режим платформы». Уровень риска правится не здесь,
 * а на «Система · Управление» рядом с запуском дня. Правки доступны при auth.dash2_full, иначе чтение.
 * Боковая панель только у расхода: что в ней, задаёт адрес и ровно один вид параметров: ?panel=swaps или ?usage=ID&days=&by= (строка расхода с периодом и разрезом). Повторный клик закрывает.
 * Отладка без сервера: ?edit=mock | mock-calm | mock-live | mock-run.
 */
const route = useRoute()
const router = useRouter()
const store = useRoleStore()
const action = useActionStore()
onMounted(() => {
  store.start()
  const dbg = new URLSearchParams(location.search).get('edit')
  if (dbg && dbg.startsWith('mock') && !store.mocked) {
    store.mock(dbg === 'mock-live' ? 'live' : 'edit')
    action.mockEdit(dbg === 'mock-calm')
  }
})
const q = (k: string): string | null => (typeof route.query[k] === 'string' && route.query[k] ? (route.query[k] as string) : null)
const swaps = computed(() => q('panel') === 'swaps')
const usage = computed(() => parseUsagePanel(route.query))
const open = computed(() => !!(swaps.value || usage.value))
const label = computed(() => (usage.value ? 'Расход группы' : 'История назначений'))
/** Переключает параметр: то же значение закрывает панель, иначе все параметры заменяются одним. */
function toggle(query: LocationQueryRaw, same: boolean): void {
  void router.replace({ query: same ? {} : query })
}
const pickUsage = (id: string, days: UsagePeriod, by: UsageBy): void => {
  const u = usage.value
  toggle(usagePanelQuery(id, days, by), !!u && u.id === id && u.days === days && u.by === by)
}
const pickSwaps = (): void => toggle({ panel: 'swaps' }, swaps.value)
function close(): void {
  void router.replace({ query: {} })
}
</script>

<template>
  <main class="page">
    <div class="col">
      <RolesBoard />
      <ProfilesBoard />
    </div>
    <div class="col">
      <ProvidersBoard />
      <ExchangeBoard />
      <UsageBoard :swaps-open="swaps" :selected="usage" @swaps="pickSwaps" @select="pickUsage" @close="close" />
      <ModeBoard />
    </div>
    <UiSidePanel :open="open" :label="label" @close="close">
      <UsagePanel v-if="usage" :key="`u${usage.id}|${usage.days}|${usage.by}`" :id="usage.id" :days="usage.days" :by="usage.by" @close="close" />
      <SwapsPanel v-else-if="swaps" @close="close" />
    </UiSidePanel>
  </main>
</template>

<style scoped>
.page { gap: calc(var(--space-6) + var(--space-2)); }
.col { min-width: 0; display: grid; gap: calc(var(--space-6) + var(--space-2)); align-content: start; }
/* от 1200 px две колонки: слева роли и профили, справа провайдеры, биржа, расход и режим */
@media (min-width: 1200px) {
  .page { grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr); column-gap: var(--space-6); align-items: start; }
}
</style>
