<script setup lang="ts">
import { computed } from 'vue'
import { THINKING, limitsText } from '@/entities/role'
import { UiModal } from '@/shared/ui'
import { useProfileForm } from '../model/useProfileForm'

/**
 * Форма профиля в окне: одна для «Добавить» (profileId = null) и «Изменить». Профиль это модель с настройками ответа: провайдер, модель, разброс ответов, длина ответа, рассуждение.
 * Пустые параметры значат «по умолчанию». Ограничения расхода относятся к провайдеру: здесь они показаны строкой для справки, менять их нужно в форме провайдера.
 */
const props = defineProps<{ open: boolean; profileId: string | null }>()
const emit = defineEmits<{ close: [] }>()
const f = useProfileForm(() => props.profileId, () => emit('close'))
const d = f.draft
const off = computed(() => f.busy.value || !f.canEdit.value)
const title = computed(() => (f.isNew.value ? 'Новый профиль' : `Профиль «${f.existing.value?.id ?? ''}»`))
const noProviders = computed(() => !f.providers.value.length)
const used = computed(() => { const u = f.existing.value?.used_by; return u && u.length ? `Используют роли: ${u.length}.` : 'Пока ни одна роль его не использует.' })
</script>

<template>
  <UiModal :open="open" :label="title" @close="emit('close')">
    <section class="f-sec" aria-labelledby="prf-h">
      <h3 id="prf-h" class="f-h">Модель</h3>
      <p v-if="!f.canEdit.value" class="f-note">Менять профили из дашборда пока нельзя: владельцу нужно включить расширенное управление.</p>
      <p v-else-if="noProviders" class="f-note">Сначала добавьте провайдера: профилю нужна модель, которую он будет запускать.</p>

      <div v-if="f.isNew.value" class="f-fld">
        <label for="prf-name">Название</label>
        <input id="prf-name" v-model="f.name.value" class="f-input" type="text" autocomplete="off" spellcheck="false" :disabled="off" :aria-invalid="!!f.errors.value.name && !!f.name.value" aria-describedby="prf-name-h">
        <p id="prf-name-h" class="f-hint">Например «Быстрый чат». Служебное имя платформа построит сама{{ f.name.value.trim() ? `: ${f.id.value}` : '' }}.</p>
      </div>
      <p v-else class="f-hint">{{ used }}</p>

      <div class="f-row2">
        <div class="f-fld">
          <label for="prf-prov">Провайдер</label>
          <select id="prf-prov" class="f-input" :value="d.provider" :disabled="off || noProviders" @change="f.setProvider(($event.target as HTMLSelectElement).value)">
            <option value="" disabled>выберите</option>
            <option v-for="p in f.providers.value" :key="p.id" :value="p.id">{{ p.name }}{{ p.enabled ? '' : ' (выключен)' }}</option>
          </select>
        </div>
        <div class="f-fld">
          <label for="prf-model">Модель</label>
          <select id="prf-model" class="f-input" :value="d.model" :disabled="off || !d.provider" @change="f.patch({ model: ($event.target as HTMLSelectElement).value })">
            <option value="" disabled>выберите</option>
            <option v-for="m in f.models.value" :key="m.id" :value="m.id">{{ m.name }}</option>
          </select>
        </div>
      </div>
      <p v-if="f.provider.value && !f.provider.value.enabled" class="f-hint warn">Этот провайдер выключен: профиль с ним не сохранится, пока провайдер не включён.</p>
      <p v-if="f.provider.value" class="f-hint">Расход у провайдера: {{ limitsText(f.provider.value.limits) }}. Ограничения меняются в форме провайдера.</p>
    </section>

    <section class="f-sec" aria-labelledby="prf-p">
      <h3 id="prf-p" class="f-h">Как отвечает</h3>
      <p class="f-hint">Пустое поле: значение по умолчанию.</p>
      <div class="f-row3">
        <div class="f-fld">
          <label for="prf-t">Разброс ответов</label>
          <input id="prf-t" v-model="d.temperature" class="f-input" type="text" inputmode="decimal" autocomplete="off" placeholder="по умолчанию" :disabled="off" :aria-invalid="!!f.errors.value.temperature" aria-describedby="prf-t-h">
          <p id="prf-t-h" class="f-hint" :class="{ bad: !!f.errors.value.temperature }">{{ f.errors.value.temperature ?? 'От 0 до 2: меньше значит строже.' }}</p>
        </div>
        <div class="f-fld">
          <label for="prf-m">Длина ответа</label>
          <input id="prf-m" v-model="d.maxTokens" class="f-input" type="text" inputmode="numeric" autocomplete="off" placeholder="по умолчанию" :disabled="off" :aria-invalid="!!f.errors.value.maxTokens" aria-describedby="prf-m-h">
          <p id="prf-m-h" class="f-hint" :class="{ bad: !!f.errors.value.maxTokens }">{{ f.errors.value.maxTokens ?? 'В токенах.' }}</p>
        </div>
        <div class="f-fld">
          <label for="prf-th">Рассуждение</label>
          <select id="prf-th" v-model="d.thinking" class="f-input" :disabled="off" aria-describedby="prf-th-h">
            <option value="">по умолчанию</option>
            <option v-for="t in THINKING" :key="t.id" :value="t.id">{{ t.label }}</option>
          </select>
          <p id="prf-th-h" class="f-hint">Сколько модель думает перед ответом.</p>
        </div>
      </div>
      <p v-if="f.lines.value.length" class="f-hint">Будет изменено: {{ f.lines.value.map((l) => `${l.label.toLowerCase()} ${l.from} → ${l.to}`).join('; ') }}.</p>
      <p v-if="f.failure.value" class="f-hint bad" role="alert">{{ f.failure.value }}</p>
    </section>

    <template #footer>
      <button type="button" class="f-ghost" :disabled="f.busy.value" @click="emit('close')">Отмена</button>
      <button type="button" class="f-cta" :disabled="!f.canSubmit.value" @click="f.submit()">{{ f.isNew.value ? 'Создать профиль' : 'Сохранить' }}</button>
    </template>
  </UiModal>
</template>
