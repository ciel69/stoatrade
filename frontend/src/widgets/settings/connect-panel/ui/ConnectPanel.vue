<script setup lang="ts">
import { computed } from 'vue'
import { credText, credsText, ctxText, exchangeStatus, limitsText, modeText, permsWarning, priceText, providerIssues, roleLabel, rolesUsing, useRoleStore } from '@/entities/role'
import { useActionStore } from '@/entities/action'
import { useControlStore } from '@/entities/control'
import { UiChip, UiDetail } from '@/shared/ui'
import { useExchangeEdit } from '../model/useExchangeEdit'
import { useProviderEdit } from '../model/useProviderEdit'
import ExchangeEdit from './ExchangeEdit.vue'
import { useProviderNet } from '../model/useProviderNet'
import ProviderAddress from './ProviderAddress.vue'
import ProviderKey from './ProviderKey.vue'
import ProviderModels from './ProviderModels.vue'
import ProviderLimits from './ProviderLimits.vue'
import ProviderSwitch from './ProviderSwitch.vue'
import './provider-edit.css'

/**
 * Боковая панель провайдера или биржи. Провайдер: где работает, ключ словами, лимиты, модели с ценой за миллион токенов, контекстом и тегами, какие роли им пользуются;
 * техническое (имя переменной окружения ключа, хост, профили) внизу. Биржа: статус, режим, исполнитель, виды ключей, лимит позиций. Значений ключей нет ни здесь, ни в данных.
 * Провайдер редактируется, когда доступно действие llm.provider (расширенное управление): включение, потолки расхода, ключ (только запись), адрес, список моделей у провайдера, проверка подключения. Иначе честная заметка и чтение.
 * Биржа остаётся только для чтения. Отладка без сервера: ?edit=mock | mock-calm.
 */
const props = defineProps<{ kind: 'provider' | 'exchange'; id: string }>()
defineEmits<{ close: [] }>()
const store = useRoleStore()
const action = useActionStore()
// отладка без сервера: ?edit=mock | mock-calm; тот же поток, что в панели роли, но для провайдеров
const dbg = new URLSearchParams(location.search).get('edit')
if (dbg && dbg.startsWith('mock') && !store.mocked) {
  store.mock(dbg === 'mock-live' ? 'live' : 'edit')
  action.mockEdit(dbg === 'mock-calm')
}
const edit = useProviderEdit(() => props.id)
const xedit = useExchangeEdit(() => props.id)
const net = useProviderNet(() => props.id, () => edit.shownKey.value)
if (dbg && dbg.startsWith('mock')) useControlStore().mockPlatform(dbg === 'mock-run')
const roles = computed(() => store.snapshot?.roles ?? [])
const p = computed(() => (props.kind === 'provider' ? (store.snapshot?.provider_list ?? []).find((x) => x.id === props.id) ?? null : null))
const e = computed(() => (props.kind === 'exchange' ? (store.snapshot?.exchange_list ?? []).find((x) => x.id === props.id) ?? null : null))
const issues = computed(() => (p.value ? providerIssues(p.value, roles.value) : []))
const users = computed(() => (p.value ? rolesUsing(p.value.id, roles.value) : []))
const verified = (v: boolean | string | null | undefined) => (v === true ? 'проверен' : typeof v === 'string' && v ? `проверен: ${v}` : v === false ? 'не проверен' : 'нет данных о проверке')
</script>

<template>
  <UiDetail :label="kind === 'provider' ? `Провайдер ${p?.name ?? id}` : `Биржа ${e?.name ?? id}`" @close="$emit('close')">
    <template #title>
      <b class="h">{{ p?.name ?? e?.name ?? id }}</b>
      <UiChip v-if="p" :tone="p.enabled ? 'ok' : 'neutral'">{{ p.enabled ? 'включён' : 'выключен' }}</UiChip>
      <UiChip v-if="e" :tone="e.active ? 'accent' : 'neutral'">{{ exchangeStatus(e) }}</UiChip>
    </template>

    <template v-if="p">
      <p class="what">{{ p.local ? 'Работает локально, на этом компьютере: данные никуда не уходят и плата не берётся.' : 'Работает в облаке: запросы уходят на сервер провайдера и оплачиваются по токенам.' }}</p>
      <ul v-if="issues.length" class="issues"><li v-for="(i, k) in issues" :key="k" :class="i.tone">{{ i.text }}</li></ul>

      <template v-if="edit.canEdit.value">
        <ProviderSwitch :edit="edit" />
        <ProviderKey :edit="edit" />
        <ProviderLimits :edit="edit" />
        <ProviderAddress :net="net" :has-key="edit.shownKey.value" />
        <ProviderModels :net="net" />
      </template>
      <template v-else>
        <p class="locked">Менять провайдеров из дашборда пока нельзя: владельцу нужно включить расширенное управление.</p>
        <section class="sec">
          <h4>Ключ и лимиты</h4>
          <dl class="kv">
            <div><dt>Ключ</dt><dd>{{ credText({ cred: p.cred, local: p.local }) }}</dd></div>
            <div v-if="permsWarning(p)"><dt>Права файла ключа</dt><dd class="warn">{{ permsWarning(p) }}</dd></div>
            <div><dt>Лимиты расхода</dt><dd>{{ limitsText(p.limits) }}</dd></div>
          </dl>
        </section>
      </template>

      <section class="sec">
        <h4>Модели ({{ p.models.length }}), цена за 1 млн токенов</h4>
        <ul v-if="p.models.length" class="models">
          <li v-for="m in p.models" :key="m.id">
            <b>{{ m.name }}</b>
            <p class="meta">{{ priceText(m.price) }}</p>
            <p class="meta">контекст {{ ctxText(m.ctx) }}{{ m.tools === true ? ' · умеет пользоваться внешними функциями' : m.tools === false ? ' · без внешних функций' : '' }}</p>
            <p v-if="m.tags.length" class="tags"><UiChip v-for="t in m.tags" :key="t">{{ t }}</UiChip></p>
          </li>
        </ul>
        <p v-else class="meta">Моделей нет.</p>
      </section>

      <section v-if="!edit.canEdit.value" class="sec">
        <h4>Кто пользуется</h4>
        <p class="meta">{{ users.length ? users.map(roleLabel).join(', ') : 'Ни одна роль не ссылается на этого провайдера.' }}</p>
      </section>

      <section class="sec">
        <h4>О провайдере</h4>
        <p v-if="p.note" class="meta">{{ p.note }}</p>
        <p class="meta">{{ verified(p.verified) }}</p>
      </section>

      <details class="tech"><summary>Для специалиста</summary><p>идентификатор {{ p.id }}{{ p.adapter ? ' · адаптер ' + p.adapter : '' }}{{ p.base_host ? ' · хост ' + p.base_host : '' }}{{ p.cred.env ? ' · переменная ключа ' + p.cred.env : '' }}{{ p.profiles.length ? ' · профили ' + p.profiles.join(', ') : '' }}</p></details>
    </template>

    <template v-else-if="e">
      <p class="what">{{ !e.implemented ? 'Эта биржа пока не поддерживается: торговать на ней нельзя.' : e.active ? 'На этой бирже сейчас торгует платформа.' : e.allowed ? 'Биржа разрешена владельцем, но сейчас не активна.' : 'Биржа не разрешена владельцем.' }}</p>
      <section class="sec">
        <h4>Сейчас</h4>
        <dl class="kv">
          <div><dt>Режим</dt><dd><UiChip :tone="e.mode === 'live' ? 'bad' : 'neutral'">{{ modeText(e.mode) }}</UiChip></dd></div>
          <div><dt>Исполнитель сделок</dt><dd>{{ e.alive == null ? 'нет данных' : e.alive ? 'работает' : 'не запущен' }}</dd></div>
          <div><dt>Ключи доступа</dt><dd>{{ credsText(e.creds) }}</dd></div>
          <div><dt>Лимит открытых позиций</dt><dd>{{ e.max_open_positions ?? '–' }}</dd></div>
        </dl>
      </section>
      <ExchangeEdit v-if="xedit.canEdit.value && e.implemented" :edit="xedit" />
      <p v-else-if="e.implemented" class="locked">Менять биржи из дашборда пока нельзя: владельцу нужно включить расширенное управление.</p>
      <section v-if="e.note" class="sec">
        <h4>Примечание</h4>
        <p class="meta">{{ e.note }}</p>
      </section>
      <details class="tech"><summary>Для специалиста</summary><p>идентификатор {{ e.id }} · {{ e.enabled ? 'включена в настройках' : 'выключена в настройках' }}</p></details>
    </template>

    <p v-else class="meta">{{ kind === 'provider' ? 'Провайдер не найден.' : 'Биржа не найдена.' }}</p>
  </UiDetail>
</template>

<style scoped>
.h { font-size: 17px; }
.what { margin: 0; font-size: 13px; line-height: 1.5; }
.issues { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; font-size: 12px; }
.issues li { display: flex; gap: var(--space-2); padding: var(--space-2) var(--space-3); border-radius: var(--radius-sm); background: var(--panel2); color: var(--muted); }
.issues li::before { content: ''; flex: none; width: 8px; height: 8px; margin-top: 5px; border-radius: 50%; background: var(--warn); }
.issues li.bad::before { background: var(--neg); }
.sec { display: grid; gap: var(--space-2); }
.sec h4 { margin: 0; font-size: 12px; font-weight: 600; color: var(--muted); }
.models { list-style: none; margin: 0; padding: 0; display: grid; gap: var(--space-3); }
.models b { font-size: 14px; overflow-wrap: anywhere; }
.meta { margin: 2px 0 0; font-size: 12px; color: var(--muted); line-height: 1.5; }
.locked { margin: 0; padding: var(--space-3); border-radius: var(--radius-sm); background: var(--panel2); font-size: 12px; color: var(--muted); line-height: 1.5; }
.tags { margin: 4px 0 0; display: flex; flex-wrap: wrap; gap: 4px; }
.kv { margin: 0; display: grid; gap: 6px; font-size: 12px; }
.kv > div { display: flex; justify-content: space-between; gap: var(--space-4); }
dt { color: var(--muted); }
dd { margin: 0; text-align: right; }
dd.warn { color: var(--warn); }
.tech { font-size: 12px; color: var(--muted); }
.tech summary { cursor: pointer; min-height: 32px; display: flex; align-items: center; }
.tech summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 2px; }
.tech p { margin: 4px 0 0; font-size: 11px; font-family: var(--mono, ui-monospace, monospace); overflow-wrap: anywhere; }
</style>
