<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useActionStore } from '@/entities/action'
import { THINKING, paramsText, presetNote, useRoleStore } from '@/entities/role'
import { UiChangeBar } from '@/shared/ui'
import { reflectText } from '@/shared/lib'
import { useExpertEdit } from '../model/useExpertEdit'

/**
 * Блок «Для специалиста» (свёрнут, на главном виде не мешает): профили моделей (модель с настройками, которую можно выбрать для ролей) и провайдер из шаблона. Замечания к конфигурации показывает «Кто думает» свёрнутым списком.
 * Профиль создаётся и правится черновиком со строкой «было → станет»; пустое поле параметра значит «не менять». Подтверждение рисков, если оно нужно, показывает общий диалог.
 * Без расширенного управления остаётся чтение. Отладка без сервера: ?edit=mock | mock-calm.
 */
const store = useRoleStore()
const action = useActionStore()
const dbg = new URLSearchParams(location.search).get('edit')
onMounted(() => {
  if (dbg && dbg.startsWith('mock') && !store.mocked) {
    store.mock('edit')
    action.mockEdit(dbg === 'mock-calm')
  }
})
const e = useExpertEdit()
const d = e.draft
const off = computed(() => e.busy.value || !e.canProfile.value)
const changeLine = computed(() => e.lines.value.map((l) => `${l.label}: ${l.from} → ${l.to}`).join('\n'))
const used = (u?: string[]): string => (u && u.length ? `используют ${u.length}` : 'пока никто не использует')
</script>

<template>
  <details class="exp">
    <summary>Для специалиста: профили моделей и новые провайдеры</summary>
    <div v-if="e.snap.value" class="in">
      <section class="sec" aria-labelledby="x-prof-h">
        <h3 id="x-prof-h" class="h">Профили моделей</h3>
        <p class="sub">Профиль это модель с настройками (разброс ответов, длина ответа, рассуждение). Профили выбираются для ролей в разделе «Кто думает».</p>
        <ul v-if="e.profiles.value.length" class="list">
          <li v-for="p in e.profiles.value" :key="p.id" class="item">
            <div class="txt">
              <b>{{ p.id }}</b>
              <span class="sub">{{ p.model_name }} · {{ paramsText(p.params) }} · {{ used(p.used_by) }}</span>
            </div>
            <button v-if="e.canProfile.value" type="button" class="btn" :aria-label="`Изменить профиль ${p.id}`" :disabled="e.busy.value" @click="e.openProfile(p.id)">Изменить</button>
          </li>
        </ul>
        <p v-else class="sub">Профилей нет.</p>
        <button v-if="e.canProfile.value && e.editing.value === null" type="button" class="btn add" :disabled="e.busy.value || !e.providers.value.length" @click="e.openNew()">Создать профиль</button>
        <p v-if="!e.canProfile.value" class="sub">Менять профили из дашборда пока нельзя: владельцу нужно включить расширенное управление.</p>

        <Transition name="reveal">
          <form v-if="e.editing.value !== null" class="form" novalidate @submit.prevent="e.submitProfile()">
            <h4 class="cap">{{ e.isNew.value ? 'Новый профиль' : `Профиль ${d.id}` }}</h4>
            <div v-if="e.isNew.value" class="fld">
              <label for="x-id">Короткое имя</label>
              <input id="x-id" v-model="d.id" class="inp" type="text" autocomplete="off" spellcheck="false" :disabled="off" :aria-invalid="!!e.errors.value.id && !!d.id" aria-describedby="x-id-h">
              <p id="x-id-h" class="sub" :class="{ bad: !!e.errors.value.id && !!d.id }">{{ d.id && e.errors.value.id ? e.errors.value.id : 'Например: fast_chat. Потом не меняется.' }}</p>
            </div>
            <div class="pair">
              <div class="fld">
                <label for="x-prov">Провайдер</label>
                <select id="x-prov" class="inp" :value="d.provider" :disabled="off" @change="e.setProvider(($event.target as HTMLSelectElement).value)">
                  <option value="" disabled>выберите</option>
                  <option v-for="p in e.providers.value" :key="p.id" :value="p.id">{{ p.name }}</option>
                </select>
              </div>
              <div class="fld">
                <label for="x-model">Модель</label>
                <select id="x-model" v-model="d.model" class="inp" :disabled="off || !d.provider">
                  <option value="" disabled>выберите</option>
                  <option v-for="m in e.models.value" :key="m.id" :value="m.id">{{ m.name }}</option>
                </select>
              </div>
            </div>
            <div class="trio">
              <div class="fld">
                <label for="x-t">Разброс ответов</label>
                <input id="x-t" v-model="d.temperature" class="inp" type="text" inputmode="decimal" autocomplete="off" placeholder="не менять" :disabled="off" :aria-invalid="!!e.errors.value.temperature" aria-describedby="x-t-h">
                <p id="x-t-h" class="sub" :class="{ bad: !!e.errors.value.temperature }">{{ e.errors.value.temperature ?? 'От 0 до 2: меньше значит строже.' }}</p>
              </div>
              <div class="fld">
                <label for="x-m">Длина ответа</label>
                <input id="x-m" v-model="d.maxTokens" class="inp" type="text" inputmode="numeric" autocomplete="off" placeholder="не менять" :disabled="off" :aria-invalid="!!e.errors.value.maxTokens" aria-describedby="x-m-h">
                <p id="x-m-h" class="sub" :class="{ bad: !!e.errors.value.maxTokens }">{{ e.errors.value.maxTokens ?? 'В токенах.' }}</p>
              </div>
              <div class="fld">
                <label for="x-th">Рассуждение</label>
                <select id="x-th" v-model="d.thinking" class="inp" :disabled="off">
                  <option value="">не менять</option>
                  <option v-for="t in THINKING" :key="t.id" :value="t.id">{{ t.label }}</option>
                </select>
              </div>
            </div>
            <UiChangeBar v-if="e.pArgs.value" :line="changeLine" effect="Профиль можно будет выбрать для ролей. Роли, которые его уже используют, сразу получат новые настройки." apply="Сохранить профиль" reset-label="Отмена" :busy="e.busy.value" @apply="e.submitProfile()" @reset="e.closeProfile()" />
            <div v-else class="acts"><button type="button" class="btn" @click="e.closeProfile()">Закрыть</button></div>
          </form>
        </Transition>
        <p class="status" role="status" aria-live="polite">{{ reflectText(e.profileWait.state.value) }}</p>
      </section>

      <section class="sec" aria-labelledby="x-add-h">
        <h3 id="x-add-h" class="h">Добавить провайдера</h3>
        <p class="sub">Готовые наборы настроек для известных сервисов. Провайдер добавляется выключенным и без ключа.</p>
        <ul v-if="e.presets.value.length" class="list">
          <li v-for="p in e.presets.value" :key="p.id" class="item">
            <div class="txt">
              <b>{{ p.name }}</b>
              <span class="sub">{{ presetNote(p) }}</span>
              <span v-if="p.note" class="sub">{{ p.note }}</span>
            </div>
            <button v-if="e.canPreset.value" type="button" class="btn" :aria-label="`Добавить провайдера ${p.name}`" :disabled="e.busy.value" @click="e.addPreset(p)">Добавить</button>
          </li>
        </ul>
        <p v-else class="sub">Все известные шаблоны уже добавлены.</p>
        <p v-if="!e.canPreset.value" class="sub">Добавлять провайдеров из дашборда пока нельзя: владельцу нужно включить расширенное управление.</p>
        <p class="status" role="status" aria-live="polite">{{ reflectText(e.presetWait.state.value) }}</p>
      </section>

    </div>
    <div v-else class="skel" aria-busy="true" />
  </details>
</template>

<style scoped>
.exp { border-top: 1px solid var(--line); padding-top: var(--space-2); }
.exp > summary { cursor: pointer; min-height: 44px; display: flex; align-items: center; font-size: 14px; font-weight: 600; }
.exp > summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 2px; }
.in { display: grid; gap: var(--space-6); padding: var(--space-3) 0 var(--space-4); max-width: 760px; }
.sec { display: grid; gap: var(--space-2); }
.h { margin: 0; font-size: 15px; font-weight: 650; }
.cap { margin: 0; font-size: 13px; font-weight: 600; }
.sub { margin: 0; font-size: 12px; line-height: 1.5; color: var(--muted); }
.sub.bad { color: var(--neg); }
.list { list-style: none; margin: 0; padding: 0; background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); }
.item { display: flex; align-items: center; justify-content: space-between; gap: var(--space-4); padding: var(--space-3) var(--space-4); }
.item + .item { border-top: 1px solid var(--line); }
.txt { min-width: 0; display: grid; gap: 2px; }
.txt b { font-size: 14px; overflow-wrap: anywhere; }
.form { display: grid; gap: var(--space-3); padding-top: var(--space-3); border-top: 1px solid var(--line); }
.fld { display: grid; gap: 4px; align-content: start; min-width: 0; }
.fld label { font-size: 12px; }
.pair { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--space-3); }
.trio { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: var(--space-3); }
.inp { min-height: 40px; padding: 0 var(--space-3); font: inherit; font-size: 14px; color: var(--text); background: var(--panel2); border: 1px solid var(--line2); border-radius: var(--radius-sm); font-variant-numeric: tabular-nums; transition: border-color var(--dur-fast) ease; }
.inp[aria-invalid='true'] { border-color: var(--neg); }
.inp:disabled { opacity: 0.45; cursor: not-allowed; }
.inp:focus-visible, .btn:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.btn { flex: none; min-height: 40px; padding: 0 var(--space-4); font: inherit; font-size: 13px; color: var(--text); background: transparent; border: 1px solid var(--line2); border-radius: var(--radius-sm); cursor: pointer; white-space: nowrap; transition: color var(--dur-fast) ease, border-color var(--dur-fast) ease, opacity var(--dur-fast) ease, transform 120ms var(--ease-out); }
.btn.add { justify-self: start; }
.btn:disabled { opacity: 0.45; cursor: not-allowed; }
.btn:active:not(:disabled) { transform: scale(0.97); }
.acts { display: flex; justify-content: flex-end; }
.status { margin: 0; font-size: 12px; color: var(--muted); }
.status:empty { display: none; }
.skel { height: 120px; border-radius: var(--radius); background: var(--panel); }
@media (max-width: 560px) {
  .pair, .trio { grid-template-columns: minmax(0, 1fr); }
  .item { flex-wrap: wrap; }
}
@media (hover: hover) and (pointer: fine) {
  .btn:hover:not(:disabled) { border-color: var(--muted); }
}
.reveal-enter-from { opacity: 0; transform: translateY(6px); }
.reveal-enter-active { transition: opacity var(--dur-enter) var(--ease-out), transform var(--dur-enter) var(--ease-out); }
.reveal-leave-to { opacity: 0; }
.reveal-leave-active { transition: opacity 120ms var(--ease-out); }
@media (prefers-reduced-motion: reduce) {
  .reveal-enter-from { transform: none; }
  .btn { transition: color var(--dur-fast) ease, border-color var(--dur-fast) ease, opacity var(--dur-fast) ease; }
  .btn:active:not(:disabled) { transform: none; }
}
</style>
