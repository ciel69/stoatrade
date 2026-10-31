<script setup lang="ts">
import { computed } from 'vue'
import { roleLabel } from '@/entities/role'
import { waitText, type ProviderEdit } from '../model/useProviderEdit'

/**
 * Переключатель «Провайдер включён» с пояснением последствий: какие роли им пользуются. Без ключа включить нельзя (причина под переключателем).
 * Выключение провайдера, которым пользуется роль live, отклонит ctl: интерфейс заранее предупреждает, решение за сервером.
 */
const props = defineProps<{ edit: ProviderEdit }>()
const e = props.edit
const on = computed(() => e.shownEnabled.value)
const reason = computed(() => (on.value ? null : e.enableReason.value))
const w = computed(() => e.waitOf('enabled'))
const who = computed(() => e.users.value.map(roleLabel).join(', '))
const effect = computed(() => {
  if (!e.users.value.length) return 'Ни одна роль не ссылается на этого провайдера: переключение ничего не ломает.'
  return on.value
    ? `Им пользуются роли: ${who.value}. Если выключить, они перейдут на запасные модели.`
    : `Раньше им пользовались роли: ${who.value}. Пока он выключен, они обходятся запасными моделями.`
})
const liveNote = computed(() => (on.value && e.liveMode.value && e.liveUsers.value.length ? `Роль, влияющая на live (${e.liveUsers.value.map(roleLabel).join(', ')}), пользуется им: платформа в live, поэтому сервер не даст его выключить.` : ''))
</script>

<template>
  <section class="pe-sec">
    <h4>Работа</h4>
    <div class="row">
      <div class="txt">
        <b id="pe-on-l">Провайдер включён</b>
        <p id="pe-on-d" class="pe-meta">{{ effect }}</p>
      </div>
      <button type="button" role="switch" class="pe-switch" :aria-checked="on" aria-labelledby="pe-on-l" aria-describedby="pe-on-d pe-on-n" :disabled="e.busy.value || !!reason" @click="e.setEnabled(!on)" />
    </div>
    <p v-if="liveNote" class="pe-meta warn">{{ liveNote }}</p>
    <p id="pe-on-n" class="pe-meta" :class="{ warn: !!reason }" aria-live="polite">{{ reason || (w ? waitText(w) : '') }}</p>
  </section>
</template>

<style scoped>
.row { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--space-4); }
.txt { min-width: 0; }
.txt b { font-size: 14px; }
.txt .pe-meta { margin-top: 2px; }
#pe-on-n:empty { display: none; }  /* живая область остаётся в разметке, пока пуста — не занимает места */
</style>
