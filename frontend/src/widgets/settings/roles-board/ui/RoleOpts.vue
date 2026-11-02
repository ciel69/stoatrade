<script setup lang="ts">
import { computed, ref } from 'vue'
import { useActionStore } from '@/entities/action'
import { emptyOpts, mockOpts, optsArgs, optsErrors, optsLines, optsReflects, useRoleStore, type OptsDraft, type RoleRow } from '@/entities/role'
import { reflectText, useReflect } from '@/shared/lib'

/**
 * Особые параметры роли («Дополнительно»): свой лимит расходов в день и параметры ответа (разброс, длина). Пустое поле значит «не менять».
 * «Вернуть стандартные» снимает все особые параметры роли разом. Изменения идут через общий движок действий (brain.role); для роли, важной для реальных денег,
 * пока платформа торгует на реальные деньги, сервер параметры не меняет, и интерфейс говорит об этом заранее.
 */
const props = defineProps<{ role: RoleRow; target: string }>()
const store = useRoleStore()
const action = useActionStore()
const draft = ref<OptsDraft>(emptyOpts())
const wait = useReflect()
const busy = computed(() => action.active)
const can = computed(() => action.can('brain.role'))
const locked = computed(() => props.role.live_risk && store.snapshot?.mode === 'live')
const errors = computed(() => optsErrors(draft.value))
const args = computed(() => optsArgs(props.target, draft.value, props.role))
const lines = computed(() => (args.value ? optsLines(args.value, props.role) : []))
const off = computed(() => busy.value || !can.value || locked.value)

function submit(): void {
  const a = args.value
  if (!a || off.value) return
  action.run({
    action: 'brain.role', args: { ...a }, title: 'Особые параметры роли', what: lines.value.map((l) => `${l.label.toLowerCase()}: ${l.from} → ${l.to}`).join('; ') + '.',
    onDone: () => {
      draft.value = emptyOpts()
      wait.begin(() => optsReflects(store.snapshot?.roles.find((x) => (x.target ?? `${x.team ?? 'shared'}.${x.role}`) === props.target), a))
      if (store.mocked) store.mockExpertApply((s) => mockOpts(s, a))
    },
  })
}
function clear(): void {
  if (off.value) return
  action.run({
    action: 'brain.role', args: { op: 'opts_clear', role: props.target }, title: 'Стандартные параметры роли',
    what: 'Особый лимит расходов и параметры ответа этой роли будут сняты, роль вернётся к настройкам профиля.',
  })
}
</script>

<template>
  <div class="opts">
    <p class="f-hint">Свой лимит расходов и параметры ответа только для этой роли. Пустое поле: не менять.</p>
    <p v-if="locked" class="f-hint warn">Пока платформа торгует на реальные деньги, параметры этой роли не меняются: сначала остановите день.</p>
    <p v-else-if="!can" class="f-hint">Менять параметры из дашборда пока нельзя: владельцу нужно включить расширенное управление.</p>
    <form v-else class="f-row3" novalidate @submit.prevent="submit">
      <div class="f-fld">
        <label for="ro-b">Лимит расходов в день, USD</label>
        <input id="ro-b" v-model="draft.budget" class="f-input" type="text" inputmode="decimal" autocomplete="off" :placeholder="role.budget_day_usd != null ? String(role.budget_day_usd) : 'не задан'" :disabled="off" :aria-invalid="!!errors.budget" aria-describedby="ro-b-h">
        <p id="ro-b-h" class="f-hint" :class="{ bad: !!errors.budget }">{{ errors.budget ?? 'Когда лимит исчерпан, роль уходит на запасные модели.' }}</p>
      </div>
      <div class="f-fld">
        <label for="ro-t">Разброс ответов</label>
        <input id="ro-t" v-model="draft.temperature" class="f-input" type="text" inputmode="decimal" autocomplete="off" :placeholder="role.primary?.params.temperature != null ? String(role.primary.params.temperature) : 'по профилю'" :disabled="off" :aria-invalid="!!errors.temperature" aria-describedby="ro-t-h">
        <p id="ro-t-h" class="f-hint" :class="{ bad: !!errors.temperature }">{{ errors.temperature ?? 'От 0 до 2.' }}</p>
      </div>
      <div class="f-fld">
        <label for="ro-m">Длина ответа, токенов</label>
        <input id="ro-m" v-model="draft.maxTokens" class="f-input" type="text" inputmode="numeric" autocomplete="off" :placeholder="role.primary?.params.max_out_tok != null ? String(role.primary.params.max_out_tok) : 'по профилю'" :disabled="off" :aria-invalid="!!errors.maxTokens" aria-describedby="ro-m-h">
        <p id="ro-m-h" class="f-hint" :class="{ bad: !!errors.maxTokens }">{{ errors.maxTokens ?? 'Целое число.' }}</p>
      </div>
    </form>
    <p v-if="lines.length && can && !locked" class="f-hint">Будет изменено: {{ lines.map((l) => `${l.label.toLowerCase()} ${l.from} → ${l.to}`).join('; ') }}.</p>
    <div v-if="can && !locked" class="f-acts">
      <button type="button" class="f-btn" :disabled="off || !args" @click="submit">Применить параметры</button>
      <button type="button" class="f-ghost" :disabled="busy" @click="clear">Вернуть стандартные</button>
    </div>
    <p class="f-hint" role="status" aria-live="polite">{{ reflectText(wait.state.value) }}</p>
  </div>
</template>

<style scoped>
.opts { display: grid; gap: var(--space-2); }
</style>
