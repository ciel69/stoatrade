<script setup lang="ts">
import { computed, ref } from 'vue'
import { useActionStore } from '@/entities/action'
import { emptyOpts, mockOpts, optsArgs, optsErrors, optsLines, optsReflects, useRoleStore, type OptsDraft, type RoleRow } from '@/entities/role'
import { UiChangeBar } from '@/shared/ui'
import { reflectText, useReflect } from '@/shared/lib'

/**
 * Особые параметры роли (для специалиста): свой лимит расходов в день и параметры ответа (разброс, длина). Пустое поле значит «не менять».
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
const line = computed(() => lines.value.map((l) => `${l.label}: ${l.from} → ${l.to}`).join('\n'))
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
  <section class="opts" aria-labelledby="ro-h">
    <h4 id="ro-h">Особые параметры роли</h4>
    <p class="meta">Свой лимит расходов и параметры ответа только для этой роли. Пустое поле: не менять.</p>
    <p v-if="locked" class="meta warn">Пока платформа торгует на реальные деньги, параметры этой роли не меняются: сначала остановите день.</p>
    <p v-else-if="!can" class="meta">Менять параметры из дашборда пока нельзя: владельцу нужно включить расширенное управление.</p>
    <form v-else class="grid" novalidate @submit.prevent="submit">
      <div class="fld">
        <label for="ro-b">Лимит расходов в день, USD</label>
        <input id="ro-b" v-model="draft.budget" class="inp" type="text" inputmode="decimal" autocomplete="off" :placeholder="role.budget_day_usd != null ? String(role.budget_day_usd) : 'не задан'" :disabled="off" :aria-invalid="!!errors.budget" aria-describedby="ro-b-h">
        <p id="ro-b-h" class="meta" :class="{ bad: !!errors.budget }">{{ errors.budget ?? 'Когда лимит исчерпан, роль уходит на запасные модели.' }}</p>
      </div>
      <div class="fld">
        <label for="ro-t">Разброс ответов</label>
        <input id="ro-t" v-model="draft.temperature" class="inp" type="text" inputmode="decimal" autocomplete="off" :placeholder="role.primary?.params.temperature != null ? String(role.primary.params.temperature) : 'по профилю'" :disabled="off" :aria-invalid="!!errors.temperature" aria-describedby="ro-t-h">
        <p id="ro-t-h" class="meta" :class="{ bad: !!errors.temperature }">{{ errors.temperature ?? 'От 0 до 2.' }}</p>
      </div>
      <div class="fld">
        <label for="ro-m">Длина ответа, токенов</label>
        <input id="ro-m" v-model="draft.maxTokens" class="inp" type="text" inputmode="numeric" autocomplete="off" :placeholder="role.primary?.params.max_out_tok != null ? String(role.primary.params.max_out_tok) : 'по профилю'" :disabled="off" :aria-invalid="!!errors.maxTokens" aria-describedby="ro-m-h">
        <p id="ro-m-h" class="meta" :class="{ bad: !!errors.maxTokens }">{{ errors.maxTokens ?? 'Целое число.' }}</p>
      </div>
    </form>
    <UiChangeBar v-if="args && can && !locked" :line="line" effect="Действует на новые вызовы роли сразу." apply="Применить" reset-label="Сбросить" :busy="busy" @apply="submit" @reset="draft = emptyOpts()" />
    <div v-if="can && !locked" class="acts"><button type="button" class="btn" :disabled="busy" @click="clear">Вернуть стандартные параметры</button></div>
    <p class="meta" role="status" aria-live="polite">{{ reflectText(wait.state.value) }}</p>
  </section>
</template>

<style scoped>
.opts { display: grid; gap: var(--space-2); font-family: var(--font, inherit); color: var(--text); }
.opts h4 { margin: 0; font-size: 12px; font-weight: 600; color: var(--muted); }
.meta { margin: 0; font-size: 12px; line-height: 1.5; color: var(--muted); }
.meta.warn { color: var(--warn); }
.meta.bad { color: var(--neg); }
.meta:empty { display: none; }
.grid { display: grid; gap: var(--space-3); }
.fld { display: grid; gap: 4px; }
.fld label { font-size: 12px; color: var(--text); }
.inp { min-height: 40px; padding: 0 var(--space-3); font: inherit; font-size: 14px; color: var(--text); background: var(--panel2); border: 1px solid var(--line2); border-radius: var(--radius-sm); font-variant-numeric: tabular-nums; transition: border-color var(--dur-fast) ease; }
.inp[aria-invalid='true'] { border-color: var(--neg); }
.inp:disabled { opacity: 0.45; cursor: not-allowed; }
.inp:focus-visible, .btn:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.acts { display: flex; }
.btn { min-height: 36px; padding: 0 var(--space-3); font: inherit; font-size: 12px; color: var(--text); background: transparent; border: 1px solid var(--line2); border-radius: var(--radius-sm); cursor: pointer; transition: color var(--dur-fast) ease, border-color var(--dur-fast) ease, opacity var(--dur-fast) ease, transform 120ms var(--ease-out); }
.btn:disabled { opacity: 0.45; cursor: not-allowed; }
.btn:active:not(:disabled) { transform: scale(0.97); }
@media (hover: hover) and (pointer: fine) {
  .btn:hover:not(:disabled) { border-color: var(--muted); }
}
@media (prefers-reduced-motion: reduce) {
  .btn { transition: color var(--dur-fast) ease, border-color var(--dur-fast) ease, opacity var(--dur-fast) ease; }
  .btn:active:not(:disabled) { transform: none; }
}
</style>
