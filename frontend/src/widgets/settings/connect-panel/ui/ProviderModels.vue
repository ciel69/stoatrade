<script setup lang="ts">
import { computed } from 'vue'
import { RE_MODEL_NAME, modelsNote, pingLine } from '@/entities/role'
import { reflectText } from '@/shared/lib'
import type { ProviderNet } from '../model/useProviderNet'

/**
 * Модели у провайдера: «Получить список» (бесплатный запрос к самому провайдеру, до 500 имён по алфавиту), поиск по подстроке, выбор имени из списка или ввод вручную, «Добавить модель»
 * (цена нулевая и возможности выключены: цену нужно сверить), и «Проверить подключение» (платный запрос на копейки). Ошибки по кодам словами, чужой текст ответа спрятан в «Что ответил сервер».
 * Ключ здесь не вводится (это ProviderKey) и не показывается. Результаты читаются из областей aria-live.
 */
const props = defineProps<{ net: ProviderNet }>()
const n = props.net
const names = computed(() => new Set((n.row.value?.models ?? []).map((m) => m.name)))
const pingBusy = computed(() => n.running.value === 'ping')
const text = computed(() => (n.models.value ? modelsNote(n.models.value, n.filtered.value.total, n.filtered.value.shown.length, n.query.value) : ''))
const why = (id: string): string => (names.value.has(id) ? 'уже добавлена' : !RE_MODEL_NAME.test(id) ? 'имя не подходит' : '')
</script>

<template>
  <section class="pe-sec" aria-labelledby="pm-h">
    <div class="pe-hrow"><h4 id="pm-h">Модели у провайдера</h4></div>
    <p class="pe-meta">Список запрашивается у самого провайдера и ничего не стоит. Выберите имя из списка или впишите его вручную.</p>

    <div class="pe-acts left">
      <button type="button" class="pe-btn" :disabled="n.busy.value || !n.ready.value" :aria-busy="n.running.value === 'models'" @click="n.fetchModels()">{{ n.running.value === 'models' ? 'Запрашиваю…' : n.models.value ? 'Обновить список' : 'Получить список' }}</button>
    </div>
    <p v-if="!n.ready.value" class="pe-meta">Сначала задайте ключ провайдера: без него он список не отдаст.</p>

    <div class="live" role="status" aria-live="polite">
      <Transition name="pe">
        <div v-if="n.modelsFail.value" class="res">
          <p class="pe-meta bad">{{ n.modelsFail.value.text }}</p>
          <details v-if="n.modelsFail.value.detail" class="more"><summary>Что ответил сервер</summary><p>{{ n.modelsFail.value.detail }}</p></details>
        </div>
      </Transition>
    </div>

    <Transition name="pe">
      <div v-if="n.models.value" class="found">
        <div class="fld">
          <label for="pm-q">Поиск по списку</label>
          <input id="pm-q" v-model="n.query.value" class="pe-input" type="text" autocomplete="off" spellcheck="false" autocapitalize="off" placeholder="часть имени, например mini" aria-describedby="pm-note">
        </div>
        <p id="pm-note" class="pe-meta" role="status" aria-live="polite">{{ text }}</p>
        <ul v-if="n.filtered.value.shown.length" class="ml" aria-label="Модели провайдера">
          <li v-for="id in n.filtered.value.shown" :key="id">
            <button type="button" class="it" :class="{ on: n.pick.value.trim() === id }" :aria-pressed="n.pick.value.trim() === id" :disabled="!!why(id)" @click="n.choose(id)">
              <span class="nm">{{ id }}</span><span v-if="why(id)" class="why">{{ why(id) }}</span>
            </button>
          </li>
        </ul>
        <p v-else class="pe-meta">Ничего не найдено: измените поиск или впишите имя вручную.</p>
      </div>
    </Transition>

    <form class="fld add" novalidate @submit.prevent="n.addModel()">
      <label for="pm-name">Имя модели у провайдера</label>
      <input id="pm-name" v-model="n.pick.value" class="pe-input" type="text" autocomplete="off" spellcheck="false" autocapitalize="off" placeholder="gpt-4o-mini" :disabled="n.busy.value" :aria-invalid="!!n.pickError.value" aria-describedby="pm-name-h">
      <p id="pm-name-h" class="pe-meta" :class="{ bad: !!n.pickError.value }">{{ n.pickError.value ?? 'Цена новой модели нулевая, а возможности выключены: сверьте цену с тарифом провайдера.' }}</p>
      <div class="pe-acts"><button type="submit" class="pe-cta" :disabled="n.busy.value || !n.canAddModel.value">{{ n.running.value === 'model' ? 'Добавляю…' : 'Добавить модель' }}</button></div>
      <p v-if="n.modelFail.value" class="pe-meta bad" role="alert">{{ n.modelFail.value }}</p>
      <p class="pe-meta" role="status" aria-live="polite">{{ reflectText(n.modelWait.state.value) }}</p>
    </form>

    <div class="sep" />
    <div class="pe-hrow"><h4 id="pp-h">Проверка подключения</h4></div>
    <p class="pe-meta">Платный запрос: модели отправляется короткий вопрос, ответ до 16 токенов. Стоит копейки.</p>
    <div class="fld">
      <label for="pp-model">Какую модель спросить</label>
      <select id="pp-model" v-model="n.pingModel.value" class="pe-input" :disabled="n.busy.value">
        <option value="">первую в списке</option>
        <option v-for="m in n.row.value?.models ?? []" :key="m.id" :value="m.id">{{ m.name }}</option>
      </select>
    </div>
    <div class="pe-acts left">
      <button type="button" class="pe-btn" :disabled="n.busy.value || !n.ready.value || !(n.row.value?.models.length)" :aria-busy="pingBusy" @click="n.runPing()">{{ pingBusy ? 'Проверяю…' : 'Проверить подключение' }}</button>
    </div>
    <div class="live" role="status" aria-live="polite">
      <Transition name="pe">
        <p v-if="n.ping.value" class="pe-meta ok">{{ pingLine(n.ping.value) }}</p>
        <div v-else-if="n.pingFail.value" class="res">
          <p class="pe-meta bad">{{ n.pingFail.value.text }}</p>
          <details v-if="n.pingFail.value.detail" class="more"><summary>Что ответил сервер</summary><p>{{ n.pingFail.value.detail }}</p></details>
        </div>
      </Transition>
    </div>
  </section>
</template>

<style scoped>
.fld { display: grid; gap: 6px; min-width: 0; }
label { font-size: 12px; color: var(--text); }
.left { justify-content: flex-start; }
.live:empty { display: none; }
.res { display: grid; gap: 4px; }
.found { display: grid; gap: var(--space-2); }
.ml { list-style: none; margin: 0; padding: 0; max-height: 176px; overflow-y: auto; overscroll-behavior: contain; border: 1px solid var(--line); border-radius: var(--radius-sm); background: var(--panel2); }
.ml li + li { border-top: 1px solid var(--line); }
.it { display: flex; align-items: baseline; justify-content: space-between; gap: var(--space-3); width: 100%; min-height: 36px; padding: 6px var(--space-3); font: inherit; font-size: 12px; text-align: left; color: var(--text); background: transparent; border: 0; cursor: pointer; transition: background-color var(--dur-fast) ease, color var(--dur-fast) ease; }
.it.on { background: var(--accent-bg); color: var(--text); }
.it:disabled { color: var(--muted); cursor: not-allowed; }
.it:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.nm { min-width: 0; overflow-wrap: anywhere; font-family: var(--mono, ui-monospace, monospace); }
.why { flex: none; font-size: 11px; color: var(--muted); }
.add { padding-top: var(--space-2); }
.sep { border-top: 1px solid var(--line); margin-top: var(--space-2); }
.more { font-size: 12px; color: var(--muted); }
.more summary { cursor: pointer; min-height: 32px; display: flex; align-items: center; }
.more summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 2px; }
.more p { margin: 4px 0 0; font-size: 11px; font-family: var(--mono, ui-monospace, monospace); overflow-wrap: anywhere; }
@media (hover: hover) and (pointer: fine) {
  .it:hover:not(:disabled):not(.on) { background: var(--panel); }
}
@media (prefers-reduced-motion: reduce) {
  .it { transition: none; }
}
</style>
