<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { keyProblem } from '@/entities/action'
import { permsWarning } from '@/entities/role'
import { UiChip } from '@/shared/ui'
import { waitText, type ProviderEdit } from '../model/useProviderEdit'

/**
 * Ключ доступа (только запись). Статус словами без маски. Ввод: поле type=password без имени и со случайным id, чтобы менеджеры паролей не предлагали его сохранить.
 * При отправке значение забирается из поля (take): поле очищается сразу, значение передаётся отправителю как аргумент и больше нигде не живёт; в стор, адрес, историю и localStorage оно не попадает.
 * Результат показывается словами по коду ответа. Удаление ключа идёт обычным действием (рискованность решает сервер).
 */
const props = defineProps<{ edit: ProviderEdit }>()
const e = props.edit
const text = ref('')
const open = ref(false)
const sending = ref(false)
const msg = ref<{ ok: boolean; text: string } | null>(null)
const input = ref<HTMLInputElement | null>(null)
const addBtn = ref<HTMLButtonElement | null>(null)
const fid = `k${Math.random().toString(36).slice(2, 10)}`
let msgTimer: ReturnType<typeof setTimeout> | undefined

const p = e.row
const needs = computed(() => !!p.value && !p.value.local && p.value.cred.needed)
const present = e.shownKey
const field = computed(() => needs.value && e.canKey.value && (open.value || !present.value))
const problem = computed(() => (text.value ? keyProblem(text.value) : null))
const valid = computed(() => !!text.value && !keyProblem(text.value))
const w = computed(() => e.waitOf('key'))
const perms = computed(() => (p.value ? permsWarning(p.value) : null))
const inUse = computed(() => e.shownEnabled.value && e.users.value.length > 0)

function wipe(): void {
  text.value = ''
  if (input.value) input.value.value = ''
}
/** Забрать значение и сразу очистить поле. */
function take(): string {
  const v = text.value
  wipe()
  return v
}
function say(ok: boolean, t: string): void {
  clearTimeout(msgTimer)
  msg.value = { ok, text: t }
  msgTimer = setTimeout(() => { msg.value = null }, ok ? 6000 : 12000)
}
async function save(): Promise<void> {
  if (!valid.value || sending.value) return
  sending.value = true
  msg.value = null
  try {
    const r = await e.saveKey(take())
    say(r.ok, r.text)
    if (r.ok) open.value = false
    // фокус не теряется: после успеха на «Заменить ключ», после отказа обратно в поле
    sending.value = false
    await nextTick()
    ;(r.ok ? addBtn.value : input.value)?.focus()
  } finally {
    sending.value = false
  }
}
function openField(): void {
  open.value = true
  msg.value = null
  void nextTick(() => input.value?.focus())
}
function closeField(): void {
  wipe()
  open.value = false
  void nextTick(() => addBtn.value?.focus())
}
/** Esc: сначала очищает введённое, затем закрывает поле замены; когда закрывать нечего, идёт дальше и закрывает панель. */
function onEsc(ev: KeyboardEvent): void {
  if (text.value) { ev.stopPropagation(); wipe() }
  else if (open.value) { ev.stopPropagation(); closeField() }
}
watch(() => p.value?.id, () => { wipe(); open.value = false; msg.value = null })
onBeforeUnmount(() => { wipe(); clearTimeout(msgTimer) })
</script>

<template>
  <section class="pe-sec" aria-labelledby="pe-key-h" @keydown.esc="onEsc">
    <div class="pe-hrow">
      <h4 id="pe-key-h">Ключ доступа</h4>
      <UiChip v-if="needs" :tone="present ? 'ok' : e.shownEnabled.value ? 'bad' : 'neutral'">{{ present ? 'ключ есть' : 'ключа нет' }}</UiChip>
    </div>

    <p v-if="!needs" class="pe-meta">{{ p?.local ? 'Ключ не нужен: провайдер работает локально.' : 'Этому провайдеру ключ не нужен.' }}</p>
    <template v-else>
      <p v-if="perms" class="pe-meta warn">{{ perms }}</p>
      <p v-if="!e.canKey.value" class="pe-locked">Ввести ключ здесь пока нельзя: владельцу нужно включить расширенное управление, либо добавить ключ в терминале командой <code>./tb llm key set {{ p?.id }}</code>.</p>

      <div v-if="e.canKey.value && present && !field" class="pe-acts left">
        <button ref="addBtn" type="button" class="pe-btn" :disabled="e.busy.value || sending" @click="openField">Заменить ключ</button>
        <button type="button" class="pe-btn danger" :disabled="e.busy.value || sending" @click="e.clearKey()">Удалить ключ</button>
      </div>
      <p v-if="e.canKey.value && present && !field && inUse" class="pe-meta">Пока провайдер включён и им пользуются роли, сервер не даст удалить ключ: сначала выключите провайдера.</p>

      <Transition name="pe">
        <form v-if="field" class="fld" autocomplete="off" novalidate @submit.prevent="save">
          <label :for="fid">{{ present ? 'Новый ключ' : 'Ключ провайдера' }}</label>
          <input
            :id="fid" ref="input" v-model="text" class="pe-input" type="password" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false"
            data-lpignore="true" data-1p-ignore="true" data-bwignore="true" :readonly="sending" :aria-invalid="!!problem" :aria-describedby="fid + '-h'" placeholder="вставьте ключ"
          >
          <p :id="fid + '-h'" class="pe-meta" :class="{ bad: !!problem }">{{ problem ?? 'Ключ записывается в файл ключей и больше нигде не показывается.' }}</p>
          <div class="pe-acts">
            <button v-if="present" type="button" class="pe-ghost" :disabled="sending" @click="closeField">Отмена</button>
            <button type="submit" class="pe-cta" :disabled="!valid || sending || e.busy.value">{{ sending ? 'Сохраняю…' : 'Сохранить ключ' }}</button>
          </div>
        </form>
      </Transition>

      <p class="pe-meta" :class="msg ? (msg.ok ? 'ok' : 'bad') : ''" role="status" aria-live="polite">{{ msg ? msg.text : w ? waitText(w) : '' }}</p>
    </template>
  </section>
</template>

<style scoped>
.fld { display: grid; gap: 6px; }
label { font-size: 12px; color: var(--text); }
.left { justify-content: flex-start; }
p[role='status']:empty { display: none; }
code { font-family: var(--mono, ui-monospace, monospace); font-size: 11px; overflow-wrap: anywhere; }
</style>
