<script setup lang="ts">
import { computed } from 'vue'
import { ADAPTERS } from '@/entities/role'
import { reflectText } from '@/shared/lib'
import type { ProviderAdd } from '../model/useProviderAdd'

/**
 * Форма «Свой провайдер»: короткое имя, тип, REST-адрес, необязательное имя файла ключа, имя первой модели и флажок «Адрес во внутренней сети».
 * Провайдер добавляется выключенным и без ключа (ключ вводится в его панели), цены новой модели нулевые: их нужно сверить. Подтверждение рисков, если оно нужно, показывает общий диалог.
 * Итог добавления читается здесь же строкой статуса (aria-live), с кнопкой «Открыть провайдера».
 */
const props = defineProps<{ add: ProviderAdd }>()
defineEmits<{ openProvider: [id: string] }>()
const a = props.add
const d = a.draft
const off = computed(() => a.busy.value)
const shown = (k: 'id' | 'baseUrl' | 'keyFile' | 'model'): string | undefined => {
  const v = k === 'id' ? d.value.id : k === 'baseUrl' ? d.value.baseUrl : k === 'keyFile' ? d.value.keyFile : d.value.model
  return v.trim() ? a.errors.value[k] : undefined
}
const idGuess = computed(() => d.value.keyFile.trim() || d.value.id || 'имя провайдера')
</script>

<template>
  <div class="pa">
    <button v-if="a.canAdd.value && !a.open.value" type="button" class="btn" :disabled="off" @click="a.openForm()">Свой провайдер</button>

    <Transition name="pa">
      <form v-if="a.open.value" class="form" novalidate aria-labelledby="pa-h" @submit.prevent="a.submit()">
        <h4 id="pa-h" class="cap">Свой провайдер</h4>
        <p class="sub">Любой сервис с совместимым REST-интерфейсом. Провайдер появится выключенным и без ключа: ключ вводится в его панели.</p>

        <div class="pair">
          <div class="fld">
            <label for="pa-id">Короткое имя</label>
            <input id="pa-id" class="inp" type="text" autocomplete="off" spellcheck="false" autocapitalize="off" :value="d.id" :disabled="off" :aria-invalid="!!shown('id')" aria-describedby="pa-id-h" @input="a.patch({ id: ($event.target as HTMLInputElement).value })">
            <p id="pa-id-h" class="sub" :class="{ bad: !!shown('id') }">{{ shown('id') ?? 'Например: my_llm. Потом не меняется.' }}</p>
          </div>
          <div class="fld">
            <label for="pa-ad">Тип интерфейса</label>
            <select id="pa-ad" class="inp" :value="d.adapter" :disabled="off" @change="a.patch({ adapter: ($event.target as HTMLSelectElement).value })">
              <option v-for="x in ADAPTERS" :key="x.id" :value="x.id">{{ x.label }}</option>
            </select>
            <p class="sub">Большинство сервисов совместимы с OpenAI.</p>
          </div>
        </div>

        <div class="fld">
          <label for="pa-url">Адрес REST (base URL)</label>
          <input id="pa-url" class="inp" type="text" inputmode="url" autocomplete="off" spellcheck="false" autocapitalize="off" placeholder="https://api.example.com/v1" :value="d.baseUrl" :disabled="off" :aria-invalid="!!shown('baseUrl')" aria-describedby="pa-url-h" @input="a.patch({ baseUrl: ($event.target as HTMLInputElement).value })">
          <p id="pa-url-h" class="sub" :class="{ bad: !!shown('baseUrl') }">{{ shown('baseUrl') ?? 'Только https. Ключ уйдёт на этот адрес, проверьте его.' }}</p>
        </div>

        <div class="pair">
          <div class="fld">
            <label for="pa-model">Модель у провайдера</label>
            <input id="pa-model" class="inp" type="text" autocomplete="off" spellcheck="false" autocapitalize="off" placeholder="gpt-4o-mini" :value="d.model" :disabled="off" :aria-invalid="!!shown('model')" aria-describedby="pa-model-h" @input="a.patch({ model: ($event.target as HTMLInputElement).value })">
            <p id="pa-model-h" class="sub" :class="{ bad: !!shown('model') }">{{ shown('model') ?? 'Первая модель обязательна. Список других можно получить у провайдера после добавления.' }}</p>
          </div>
          <div class="fld">
            <label for="pa-kf">Файл ключа, необязательно</label>
            <input id="pa-kf" class="inp" type="text" autocomplete="off" spellcheck="false" autocapitalize="off" :placeholder="idGuess" :value="d.keyFile" :disabled="off" :aria-invalid="!!shown('keyFile')" aria-describedby="pa-kf-h" @input="a.patch({ keyFile: ($event.target as HTMLInputElement).value })">
            <p id="pa-kf-h" class="sub" :class="{ bad: !!shown('keyFile') }">{{ shown('keyFile') ?? 'Только имя файла. Если пусто, берётся имя провайдера.' }}</p>
          </div>
        </div>

        <div class="chk">
          <input id="pa-priv" type="checkbox" :checked="d.allowPrivate" :disabled="off" aria-describedby="pa-priv-h" @change="a.patch({ allowPrivate: ($event.target as HTMLInputElement).checked })">
          <label for="pa-priv">Адрес во внутренней сети</label>
        </div>
        <p id="pa-priv-h" class="sub">Для сервера у вас дома или в офисе. Запросы и ключ смогут уходить внутрь сети, поэтому платформа попросит отдельное подтверждение.</p>
        <Transition name="pa">
          <p v-if="a.privateHint.value" class="sub warn" role="status">Адрес похож на внутренний. Без флажка выше платформа его не примет.</p>
        </Transition>

        <p v-if="a.failure.value" class="sub bad" role="alert">{{ a.failure.value }}</p>
        <div class="acts">
          <button type="button" class="ghost" :disabled="off" @click="a.close()">Отмена</button>
          <button type="submit" class="cta" :disabled="off || !a.args.value">Добавить провайдера</button>
        </div>
      </form>
    </Transition>

    <div class="res" role="status" aria-live="polite">
      <template v-if="a.created.value && !a.open.value">
        <p class="sub ok">Провайдер «{{ a.created.value }}» добавлен: выключен, ключа нет. Цены его модели нулевые: сверьте их с тарифом.<span v-if="reflectText(a.wait.state.value)"> {{ reflectText(a.wait.state.value) }}</span></p>
        <button type="button" class="btn" @click="$emit('openProvider', a.created.value!)">Открыть провайдера</button>
      </template>
    </div>
  </div>
</template>

<style scoped>
.pa { display: grid; gap: var(--space-2); }
.sub { margin: 0; font-size: 12px; line-height: 1.5; color: var(--muted); }
.sub.bad { color: var(--neg); }
.sub.warn { color: var(--warn); }
.sub.ok { color: var(--text); }
.cap { margin: 0; font-size: 13px; font-weight: 600; }
.form { display: grid; gap: var(--space-3); padding-top: var(--space-3); border-top: 1px solid var(--line); }
.fld { display: grid; gap: 4px; align-content: start; min-width: 0; }
.fld label, .chk label { font-size: 12px; }
.pair { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--space-3); }
.inp { min-height: 40px; padding: 0 var(--space-3); font: inherit; font-size: 14px; color: var(--text); background: var(--panel2); border: 1px solid var(--line2); border-radius: var(--radius-sm); min-width: 0; transition: border-color var(--dur-fast) ease; }
.inp[aria-invalid='true'] { border-color: var(--neg); }
.inp:disabled { opacity: 0.45; cursor: not-allowed; }
.inp::placeholder { color: var(--muted); opacity: 0.8; }
.inp:focus-visible, .btn:focus-visible, .ghost:focus-visible, .cta:focus-visible, .chk input:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.chk { display: flex; align-items: center; gap: var(--space-2); min-height: 40px; }
.chk input { flex: none; width: 18px; height: 18px; margin: 0; accent-color: var(--accent); }
.chk label { cursor: pointer; font-size: 13px; }
.btn, .ghost, .cta { flex: none; min-height: 40px; padding: 0 var(--space-4); font: inherit; font-size: 13px; border-radius: var(--radius-sm); cursor: pointer; white-space: nowrap; transition: color var(--dur-fast) ease, border-color var(--dur-fast) ease, opacity var(--dur-fast) ease, filter var(--dur-fast) ease, transform 120ms var(--ease-out); }
.btn { justify-self: start; color: var(--text); background: transparent; border: 1px solid var(--line2); }
.ghost { color: var(--muted); background: transparent; border: 1px solid var(--line2); }
.cta { color: var(--bg); background: var(--accent); border: 0; font-weight: 650; }
.btn:disabled, .ghost:disabled, .cta:disabled { opacity: 0.45; cursor: not-allowed; }
.btn:active:not(:disabled), .ghost:active:not(:disabled), .cta:active:not(:disabled) { transform: scale(0.97); }
.acts { display: flex; justify-content: flex-end; gap: var(--space-2); }
.res:empty { display: none; }
.res { display: grid; gap: var(--space-2); }
.pa-enter-from { opacity: 0; transform: translateY(6px); }
.pa-enter-active { transition: opacity var(--dur-enter) var(--ease-out), transform var(--dur-enter) var(--ease-out); }
.pa-leave-to { opacity: 0; }
.pa-leave-active { transition: opacity 120ms var(--ease-out); }
@media (max-width: 560px) {
  .pair { grid-template-columns: minmax(0, 1fr); }
  .acts { flex-direction: column-reverse; }
  .btn { justify-self: stretch; }
}
@media (hover: hover) and (pointer: fine) {
  .btn:hover:not(:disabled), .ghost:hover:not(:disabled) { border-color: var(--muted); color: var(--text); }
  .cta:hover:not(:disabled) { filter: brightness(1.08); }
}
@media (prefers-reduced-motion: reduce) {
  .pa-enter-from { transform: none; }
  .btn, .ghost, .cta { transition: color var(--dur-fast) ease, border-color var(--dur-fast) ease, opacity var(--dur-fast) ease; }
  .btn:active:not(:disabled), .ghost:active:not(:disabled), .cta:active:not(:disabled) { transform: none; }
}
</style>
