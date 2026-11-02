<script setup lang="ts">
import { computed } from 'vue'
import { RE_MODEL_NAME, modelsNote, pingLine } from '@/entities/role'
import { reflectText } from '@/shared/lib'
import type { ProviderNet } from '../model/useProviderNet'

/**
 * Модели провайдера и проверка подключения. «Получить список моделей» (бесплатный запрос к самому провайдеру, до 500 имён по алфавиту), поиск по подстроке, выбор имени из списка
 * или ввод вручную, «Добавить» (цена нулевая и возможности выключены: цену нужно сверить), и «Проверить подключение» (платный запрос на копейки, всегда первой моделью провайдера).
 * Ошибки по кодам словами, чужой текст ответа спрятан в «Что ответил сервер». Ключ здесь не вводится (это ProviderKey) и не показывается. Результаты читаются из областей aria-live.
 */
const props = defineProps<{ net: ProviderNet }>()
const n = props.net
const names = computed(() => new Set((n.row.value?.models ?? []).map((m) => m.name)))
const current = computed(() => (n.row.value?.models ?? []).map((m) => m.name))
const pingBusy = computed(() => n.running.value === 'ping')
const text = computed(() => (n.models.value ? modelsNote(n.models.value, n.filtered.value.total, n.filtered.value.shown.length, n.query.value) : ''))
const why = (id: string): string => (names.value.has(id) ? 'уже добавлена' : !RE_MODEL_NAME.test(id) ? 'имя не подходит' : '')
</script>

<template>
  <section class="f-sec" aria-labelledby="pm-h">
    <h3 id="pm-h" class="f-h">Модели</h3>
    <p class="f-hint">{{ current.length ? `Сейчас добавлено: ${current.join(', ')}.` : 'Моделей пока нет.' }}</p>

    <div class="f-acts">
      <button type="button" class="f-btn" :disabled="n.busy.value || !n.ready.value" :aria-busy="n.running.value === 'models'" @click="n.fetchModels()">{{ n.running.value === 'models' ? 'Запрашиваю…' : n.models.value ? 'Обновить список моделей' : 'Получить список моделей' }}</button>
    </div>
    <p v-if="!n.ready.value" class="f-hint">Сначала задайте ключ провайдера: без него он список не отдаст.</p>
    <p v-else class="f-hint">Список запрашивается у самого провайдера и ничего не стоит.</p>

    <div class="live" role="status" aria-live="polite">
      <Transition name="f">
        <div v-if="n.modelsFail.value" class="res">
          <p class="f-hint bad">{{ n.modelsFail.value.text }}</p>
          <details v-if="n.modelsFail.value.detail" class="more"><summary>Что ответил сервер</summary><p>{{ n.modelsFail.value.detail }}</p></details>
        </div>
      </Transition>
    </div>

    <Transition name="f">
      <div v-if="n.models.value" class="found">
        <div class="f-fld">
          <label for="pm-q">Поиск по списку</label>
          <input id="pm-q" v-model="n.query.value" class="f-input" type="text" autocomplete="off" spellcheck="false" autocapitalize="off" placeholder="часть имени, например mini" aria-describedby="pm-note">
        </div>
        <p id="pm-note" class="f-hint" role="status" aria-live="polite">{{ text }}</p>
        <ul v-if="n.filtered.value.shown.length" class="ml" aria-label="Модели провайдера">
          <li v-for="id in n.filtered.value.shown" :key="id">
            <button type="button" class="it" :class="{ on: n.pick.value.trim() === id }" :aria-pressed="n.pick.value.trim() === id" :disabled="!!why(id)" @click="n.choose(id)">
              <span class="nm">{{ id }}</span><span v-if="why(id)" class="why">{{ why(id) }}</span>
            </button>
          </li>
        </ul>
        <p v-else class="f-hint">Ничего не найдено: измените поиск или впишите имя вручную.</p>
      </div>
    </Transition>

    <form class="f-fld add" novalidate @submit.prevent="n.addModel()">
      <label for="pm-name">Имя модели у провайдера</label>
      <div class="inline">
        <input id="pm-name" v-model="n.pick.value" class="f-input" type="text" autocomplete="off" spellcheck="false" autocapitalize="off" placeholder="gpt-4o-mini" :disabled="n.busy.value" :aria-invalid="!!n.pickError.value" aria-describedby="pm-name-h">
        <button type="submit" class="f-btn" :disabled="n.busy.value || !n.canAddModel.value">{{ n.running.value === 'model' ? 'Добавляю…' : 'Добавить' }}</button>
      </div>
      <p id="pm-name-h" class="f-hint" :class="{ bad: !!n.pickError.value }">{{ n.pickError.value ?? 'Выберите из списка или впишите. Цена новой модели нулевая: сверьте её с тарифом провайдера.' }}</p>
      <p v-if="n.modelFail.value" class="f-hint bad" role="alert">{{ n.modelFail.value }}</p>
      <p class="f-hint" role="status" aria-live="polite">{{ reflectText(n.modelWait.state.value) }}</p>
    </form>
  </section>

  <section class="f-sec" aria-labelledby="pp-h">
    <h3 id="pp-h" class="f-h">Проверка подключения</h3>
    <p class="f-hint">Платный запрос: первой модели отправляется короткий вопрос, ответ до 16 токенов. Стоит копейки.</p>
    <div class="f-acts">
      <button type="button" class="f-btn" :disabled="n.busy.value || !n.ready.value || !(n.row.value?.models.length)" :aria-busy="pingBusy" @click="n.runPing()">{{ pingBusy ? 'Проверяю…' : 'Проверить подключение' }}</button>
    </div>
    <div class="live" role="status" aria-live="polite">
      <Transition name="f">
        <p v-if="n.ping.value" class="f-hint ok">{{ pingLine(n.ping.value) }}</p>
        <div v-else-if="n.pingFail.value" class="res">
          <p class="f-hint bad">{{ n.pingFail.value.text }}</p>
          <details v-if="n.pingFail.value.detail" class="more"><summary>Что ответил сервер</summary><p>{{ n.pingFail.value.detail }}</p></details>
        </div>
      </Transition>
    </div>
  </section>
</template>

<style scoped>
.live:empty { display: none; }
.res { display: grid; gap: 4px; }
.found { display: grid; gap: var(--space-2); }
.inline { display: flex; gap: var(--space-2); align-items: start; }
.ml { list-style: none; margin: 0; padding: 0; max-height: 176px; overflow-y: auto; overscroll-behavior: contain; border: 1px solid var(--line); border-radius: var(--radius-sm); background: var(--panel2); }
.ml li + li { border-top: 1px solid var(--line); }
.it { display: flex; align-items: baseline; justify-content: space-between; gap: var(--space-3); width: 100%; min-height: 36px; padding: 6px var(--space-3); font: inherit; font-size: 12px; text-align: left; color: var(--text); background: transparent; border: 0; cursor: pointer; transition: background-color var(--dur-fast) ease, color var(--dur-fast) ease; }
.it.on { background: var(--accent-bg); color: var(--text); }
.it:disabled { color: var(--muted); cursor: not-allowed; }
.it:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.nm { min-width: 0; overflow-wrap: anywhere; font-family: var(--mono, ui-monospace, monospace); }
.why { flex: none; font-size: 11px; color: var(--muted); }
.more { font-size: 12px; color: var(--muted); }
.more summary { cursor: pointer; min-height: 32px; display: flex; align-items: center; }
.more summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 2px; }
.more p { margin: 4px 0 0; font-size: 11px; font-family: var(--mono, ui-monospace, monospace); overflow-wrap: anywhere; }
@media (max-width: 560px) {
  .inline { flex-direction: column; }
  .inline .f-btn { width: 100%; }
}
@media (hover: hover) and (pointer: fine) {
  .it:hover:not(:disabled):not(.on) { background: var(--panel); }
}
@media (prefers-reduced-motion: reduce) {
  .it { transition: none; }
}
</style>
