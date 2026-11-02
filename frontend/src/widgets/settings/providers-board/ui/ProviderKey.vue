<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { keyProblem } from '@/entities/action'
import { permsWarning } from '@/entities/role'
import { UiChip } from '@/shared/ui'
import { waitText, type ProviderEdit } from '../model/useProviderEdit'

/**
 * Ключ доступа (только запись). Статус словами без маски. Ввод: поле type=password без имени и со случайным id, чтобы менеджеры паролей не предлагали его сохранить.
 * При отправке значение забирается из поля (take): поле очищается сразу, значение передаётся отправителю как аргумент и больше нигде не живёт; в стор, адрес, историю и localStorage оно не попадает.
 * Без расширенного управления поля нет, вместо него команда для терминала. Результат показывается словами по коду ответа. Удаление ключа идёт обычным действием (рискованность решает сервер).
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
/** Esc: сначала очищает введённое, затем закрывает поле замены; когда закрывать нечего, идёт дальше и закрывает окно. */
function onEsc(ev: KeyboardEvent): void {
  if (text.value) { ev.stopPropagation(); wipe() }
  else if (open.value) { ev.stopPropagation(); closeField() }
}
watch(() => p.value?.id, () => { wipe(); open.value = false; msg.value = null })
onBeforeUnmount(() => { wipe(); clearTimeout(msgTimer) })
</script>

<template>
  <section class="f-sec" aria-labelledby="pk-h" @keydown.esc="onEsc">
    <div class="f-hrow">
      <h3 id="pk-h" class="f-h">Ключ доступа</h3>
      <UiChip v-if="needs" :tone="present ? 'ok' : e.shownEnabled.value ? 'bad' : 'neutral'">{{ present ? 'ключ есть' : 'ключа нет' }}</UiChip>
    </div>

    <p v-if="!needs" class="f-hint">{{ p?.local ? 'Ключ не нужен: провайдер работает на этом компьютере.' : 'Этому провайдеру ключ не нужен.' }}</p>
    <template v-else>
      <p v-if="perms" class="f-hint warn">{{ perms }}</p>
      <p v-if="!e.canKey.value" class="f-note">Ввести ключ здесь пока нельзя: владельцу нужно включить расширенное управление. Либо добавьте ключ в терминале командой <code>./tb llm key set {{ p?.id }}</code>.</p>

      <div v-if="e.canKey.value && present && !field" class="f-acts">
        <button ref="addBtn" type="button" class="f-btn" :disabled="e.busy.value || sending" @click="openField">Заменить ключ</button>
        <button type="button" class="f-btn danger" :disabled="e.busy.value || sending" @click="e.clearKey()">Удалить ключ</button>
      </div>
      <p v-if="e.canKey.value && present && !field && inUse" class="f-hint">Пока провайдер включён и им пользуются роли, сервер не даст удалить ключ: сначала выключите провайдера.</p>

      <Transition name="f">
        <form v-if="field" class="fld" autocomplete="off" novalidate @submit.prevent="save">
          <label class="f-lab" :for="fid">{{ present ? 'Новый ключ' : 'Ключ провайдера' }}</label>
          <input
            :id="fid" ref="input" v-model="text" class="f-input" type="password" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false"
            data-lpignore="true" data-1p-ignore="true" data-bwignore="true" :readonly="sending" :aria-invalid="!!problem" :aria-describedby="fid + '-h'" placeholder="вставьте ключ"
          >
          <p :id="fid + '-h'" class="f-hint" :class="{ bad: !!problem }">{{ problem ?? 'Ключ записывается в файл ключей и больше нигде не показывается.' }}</p>
          <div class="f-acts">
            <button type="submit" class="f-cta" :disabled="!valid || sending || e.busy.value">{{ sending ? 'Сохраняю…' : 'Сохранить ключ' }}</button>
            <button v-if="present" type="button" class="f-ghost" :disabled="sending" @click="closeField">Отмена</button>
          </div>
        </form>
      </Transition>

      <p class="f-hint" :class="msg ? (msg.ok ? 'ok' : 'bad') : ''" role="status" aria-live="polite">{{ msg ? msg.text : w ? waitText(w) : '' }}</p>
    </template>
  </section>
</template>

<style scoped>
.fld { display: grid; gap: 6px; }
code { font-family: var(--mono, ui-monospace, monospace); font-size: 11px; overflow-wrap: anywhere; }
</style>
