<script setup lang="ts">
import { computed, watch } from 'vue'
import { ADAPTERS, LIMIT_FIELDS, limitsText, roleLabel } from '@/entities/role'
import { UiChip, UiModal } from '@/shared/ui'
import { reflectText } from '@/shared/lib'
import { useProviderEdit, waitText } from '../model/useProviderEdit'
import { useProviderForm } from '../model/useProviderForm'
import { useProviderNet } from '../model/useProviderNet'
import ProviderKey from './ProviderKey.vue'
import ProviderModels from './ProviderModels.vue'

/**
 * Форма провайдера в окне: одна и та же для «Добавить» (providerId = null) и «Изменить». Основное (название, тип, адрес, внутренняя сеть, первая модель при создании, ограничения расхода при правке)
 * сохраняется одной кнопкой; ключ, модели, проверка подключения и «Включён» это отдельные действия со своими кнопками. После создания окно само переходит к правке созданного провайдера
 * (событие created), чтобы сразу ввести ключ и добавить модели. Подтверждение рисков показывает общий диалог поверх окна.
 */
const props = defineProps<{ open: boolean; providerId: string | null; intro?: string }>()
const emit = defineEmits<{ close: []; created: [id: string] }>()
const id = () => props.providerId
const f = useProviderForm(id)
const edit = useProviderEdit(() => id() ?? '')
const net = useProviderNet(() => id() ?? '', () => edit.shownKey.value)
const d = f.draft
const r = f.row
const off = computed(() => f.busy.value || !f.canEdit.value)

watch(f.createdReady, (v) => { if (v && f.created.value) emit('created', f.created.value) })

/** Тип, которого нет в списке (необычный адаптер из конфигурации), показывается как есть, чтобы поле не оставалось пустым. */
const adapters = computed(() => (ADAPTERS.some((x) => x.id === d.value.adapter) ? ADAPTERS : [...ADAPTERS, { id: d.value.adapter, label: d.value.adapter }]))
const title = computed(() => (f.isNew.value ? 'Новый провайдер' : (r.value?.name ?? 'Провайдер')))
const limitsLine = computed(() => limitsText(r.value?.limits))
const on = computed(() => edit.shownEnabled.value)
const who = computed(() => edit.users.value.map(roleLabel).join(', '))
const enableEffect = computed(() => {
  if (!edit.users.value.length) return 'Ни одна роль пока не ссылается на этого провайдера: переключение ничего не ломает.'
  return on.value ? `Им пользуются роли: ${who.value}. Если выключить, они перейдут на запасные модели.` : `Раньше им пользовались роли: ${who.value}. Пока он выключен, они обходятся запасными моделями.`
})
const liveNote = computed(() => (on.value && edit.liveMode.value && edit.liveUsers.value.length ? `Им пользуется роль, от которой зависит торговля на реальные деньги (${edit.liveUsers.value.map(roleLabel).join(', ')}). Платформа сейчас торгует на реальные деньги, поэтому сервер не даст его выключить.` : ''))
const enableReason = computed(() => (on.value ? null : edit.enableReason.value))
const ew = computed(() => edit.waitOf('enabled'))
const saveWait = computed(() => reflectText(f.saveWait.state.value))
</script>

<template>
  <UiModal :open="open" :label="title" @close="emit('close')">
    <template #chip>
      <UiChip v-if="r" :tone="on ? 'ok' : 'neutral'">{{ on ? 'включён' : 'выключен' }}</UiChip>
    </template>

    <section class="f-sec" aria-labelledby="pf-h">
      <h3 id="pf-h" class="f-h">Основное</h3>
      <p v-if="intro" class="f-note">{{ intro }}</p>
      <p v-if="!f.canEdit.value" class="f-note">Менять провайдеров из дашборда пока нельзя: владельцу нужно включить расширенное управление.</p>

      <div class="f-fld">
        <label for="pf-name">Название</label>
        <input id="pf-name" class="f-input" type="text" autocomplete="off" spellcheck="false" :value="d.name" :disabled="off" :aria-invalid="!!f.shown('name')" aria-describedby="pf-name-h" @input="f.setField({ name: ($event.target as HTMLInputElement).value })">
        <p id="pf-name-h" class="f-hint" :class="{ bad: !!f.shown('name') }">{{ f.shown('name') ?? (f.isNew.value ? 'Любое понятное имя, например «Мой сервер». Служебное имя платформа построит сама.' : '') }}</p>
      </div>

      <div class="f-fld">
        <label for="pf-ad">Тип</label>
        <select id="pf-ad" class="f-input" :value="d.adapter" :disabled="off || !f.isNew.value" aria-describedby="pf-ad-h" @change="f.setField({ adapter: ($event.target as HTMLSelectElement).value })">
          <option v-for="x in adapters" :key="x.id" :value="x.id">{{ x.label }}</option>
        </select>
        <p id="pf-ad-h" class="f-hint">{{ f.isNew.value ? 'Большинство сервисов совместимы с OpenAI.' : 'После создания тип не меняется.' }}</p>
      </div>

      <div class="f-fld">
        <label for="pf-url">Адрес сервиса (REST)</label>
        <input id="pf-url" class="f-input" type="text" inputmode="url" autocomplete="off" spellcheck="false" autocapitalize="off" :placeholder="f.isNew.value ? 'https://api.example.com/v1' : `сейчас ${r?.base_host ?? 'не указан'}; пусто: не менять`" :value="d.baseUrl" :disabled="off" :aria-invalid="!!f.shown('baseUrl')" aria-describedby="pf-url-h" @input="f.setField({ baseUrl: ($event.target as HTMLInputElement).value })">
        <p id="pf-url-h" class="f-hint" :class="{ bad: !!f.shown('baseUrl'), warn: !f.shown('baseUrl') && !f.isNew.value && !!r?.cred.present && !!d.baseUrl.trim() }">{{ f.shown('baseUrl') ?? (!f.isNew.value && r?.cred.present && d.baseUrl.trim() ? 'Ключ провайдера после смены будет уходить на новый адрес. Убедитесь, что это нужный сервис.' : 'Только https. Ключ уходит на этот адрес, проверьте его.') }}</p>
      </div>

      <div class="f-chk">
        <input id="pf-priv" type="checkbox" :checked="d.allowPrivate" :disabled="off" aria-describedby="pf-priv-h" @change="f.setField({ allowPrivate: ($event.target as HTMLInputElement).checked })">
        <label for="pf-priv">Адрес во внутренней сети</label>
      </div>
      <p id="pf-priv-h" class="f-hint">Для сервера у вас дома или в офисе. Запросы и ключ смогут уходить внутрь сети, поэтому платформа попросит отдельное подтверждение.</p>
      <Transition name="f">
        <p v-if="f.privateHint.value" class="f-hint warn" role="status">Адрес похож на внутренний. Без флажка выше платформа его не примет.</p>
      </Transition>

      <div v-if="f.isNew.value" class="f-fld">
        <label for="pf-model">Первая модель</label>
        <input id="pf-model" class="f-input" type="text" autocomplete="off" spellcheck="false" autocapitalize="off" placeholder="gpt-4o-mini" :value="d.model" :disabled="off" :aria-invalid="!!f.shown('model')" aria-describedby="pf-model-h" @input="f.setField({ model: ($event.target as HTMLInputElement).value })">
        <p id="pf-model-h" class="f-hint" :class="{ bad: !!f.shown('model') }">{{ f.shown('model') ?? 'Имя модели так, как его называет провайдер. Остальные можно будет выбрать из списка после ввода ключа.' }}</p>
      </div>

      <details v-else class="f-more">
        <summary>Ограничения расхода <span class="f-sum">{{ limitsLine }}</span></summary>
        <p class="f-hint">Когда ограничение исчерпано, запросы к провайдеру останавливаются до конца часа или суток. Пустое поле: без ограничения.</p>
        <div class="f-row2 lim">
          <div v-for="x in LIMIT_FIELDS" :key="x.key" class="f-fld">
            <label :for="`pf-l-${x.key}`">{{ x.label }}{{ x.unit ? `, ${x.unit}` : '' }}</label>
            <input :id="`pf-l-${x.key}`" class="f-input" type="text" :inputmode="x.kind === 'usd' ? 'decimal' : 'numeric'" autocomplete="off" spellcheck="false" placeholder="без ограничения" :value="f.limitText(x.key)" :disabled="off" :aria-invalid="!!f.errors.value.limits?.[x.key]" :aria-describedby="`pf-l-${x.key}-h`" @input="f.setLimit(x.key, ($event.target as HTMLInputElement).value)">
            <p :id="`pf-l-${x.key}-h`" class="f-hint" :class="{ bad: !!f.errors.value.limits?.[x.key] }">{{ f.errors.value.limits?.[x.key] ?? '' }}</p>
          </div>
        </div>
      </details>

      <p v-if="f.lines.value.length" class="f-hint">Будет изменено: {{ f.lines.value.join('; ') }}.</p>
      <p v-if="f.failure.value" class="f-hint bad" role="alert">{{ f.failure.value }}</p>
      <p class="f-hint" :class="{ warn: !!f.note.value }" role="status" aria-live="polite">{{ f.note.value || (f.created.value ? (f.createdReady.value ? '' : 'Провайдер создан, ожидаем обновления данных…') : saveWait) }}</p>
    </section>

    <template v-if="!f.isNew.value && r">
      <ProviderKey :edit="edit" />
      <ProviderModels :net="net" />

      <section class="f-sec" aria-labelledby="pe-h">
        <h3 id="pe-h" class="f-h">Работа</h3>
        <div class="row">
          <div class="txt">
            <b id="pe-on-l">Провайдер включён</b>
            <p id="pe-on-d" class="f-hint">{{ enableEffect }}</p>
          </div>
          <button type="button" role="switch" class="f-switch" :aria-checked="on" aria-labelledby="pe-on-l" aria-describedby="pe-on-d pe-on-n" :disabled="edit.busy.value || !!enableReason || !edit.canEdit.value" @click="edit.setEnabled(!on)" />
        </div>
        <p v-if="liveNote" class="f-hint warn">{{ liveNote }}</p>
        <p id="pe-on-n" class="f-hint" :class="{ warn: !!enableReason }" aria-live="polite">{{ enableReason || (ew ? waitText(ew) : '') }}</p>
      </section>
    </template>

    <template #footer>
      <button type="button" class="f-ghost" :disabled="f.busy.value" @click="emit('close')">{{ f.isNew.value ? 'Отмена' : 'Закрыть' }}</button>
      <button type="button" class="f-cta" :disabled="!f.canSubmit.value" @click="f.submit()">{{ f.isNew.value ? 'Создать провайдера' : 'Сохранить' }}</button>
    </template>
  </UiModal>
</template>

<style scoped>
.row { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--space-4); }
.txt { min-width: 0; }
.txt b { font-size: 14px; }
.txt .f-hint { margin-top: 2px; }
.lim { margin-top: var(--space-2); }
</style>
