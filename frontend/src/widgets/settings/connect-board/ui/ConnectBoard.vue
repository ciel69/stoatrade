<script setup lang="ts">
import { computed } from 'vue'
import { connectSummary, credText, credsText, exchangeStatus, limitsText, modeText, modelsText, providerIssues, useRoleStore } from '@/entities/role'
import { UiChip } from '@/shared/ui'

/**
 * Блок «Что подключено»: провайдеры LLM (где работает, включён ли, есть ли ключ, лимиты расхода) и биржи (какая активна, в каком режиме, жив ли исполнитель, какие ключи настроены).
 * Сверху вывод одной фразой, замечания только когда есть что сказать (провайдер выключен, а им пользуется роль; нет ключа; слабые права файла ключа).
 * Ключи не показываются вообще, только факт «ключ есть». Подробности по клику в боковой панели. Только чтение.
 */
defineProps<{ provider: string | null; exchange: string | null }>()
const emit = defineEmits<{ provider: [id: string]; exchange: [id: string] }>()
const store = useRoleStore()
const s = computed(() => store.snapshot)
const providers = computed(() => s.value?.provider_list ?? [])
const exchanges = computed(() => s.value?.exchange_list ?? [])
const line = computed(() => connectSummary(providers.value, exchanges.value))
const issues = (p: (typeof providers.value)[number]) => providerIssues(p, s.value?.roles ?? [])
</script>

<template>
  <section class="board" aria-label="Что подключено">
    <header class="top">
      <h2 class="title">Что подключено</h2>
    </header>

    <template v-if="s && (providers.length || exchanges.length)">
      <div class="sum"><p class="line">{{ line }}</p></div>

      <section class="group">
        <h3 class="cap">Провайдеры LLM: кто отвечает за мозг</h3>
        <ul v-if="providers.length" class="rows">
          <li v-for="p in providers" :key="p.id">
            <button type="button" class="row" :class="{ sel: provider === p.id }" :aria-pressed="provider === p.id" @click="emit('provider', p.id)">
              <span class="who">
                <b>{{ p.name }}</b>
                <UiChip :tone="p.enabled ? 'ok' : 'neutral'">{{ p.enabled ? 'включён' : 'выключен' }}</UiChip>
                <span class="what">{{ p.local ? 'работает локально, на этом компьютере' : 'работает в облаке' }} · {{ modelsText(p.models.length) }}</span>
              </span>
              <span class="mid">
                <span :class="{ bad: p.cred.needed && !p.cred.present && p.enabled }">{{ credText({ cred: p.cred, local: p.local }) }}</span>
                <span v-for="(i, k) in issues(p)" :key="k" class="note" :class="i.tone"><span class="dot" aria-hidden="true" />{{ i.text }}</span>
              </span>
              <span class="lim">{{ limitsText(p.limits) }}</span>
              <svg class="chev" viewBox="0 0 12 12" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4.5 3L7.5 6L4.5 9" /></svg>
            </button>
          </li>
        </ul>
        <p v-else class="empty">Провайдеры не настроены. Они описаны в config/providers.json.</p>
      </section>

      <section class="group">
        <h3 class="cap">Биржи: где торгует платформа</h3>
        <ul v-if="exchanges.length" class="rows">
          <li v-for="e in exchanges" :key="e.id">
            <button type="button" class="row" :class="{ sel: exchange === e.id }" :aria-pressed="exchange === e.id" @click="emit('exchange', e.id)">
              <span class="who">
                <b>{{ e.name }}</b>
                <UiChip :tone="e.active ? 'accent' : 'neutral'">{{ exchangeStatus(e) }}</UiChip>
                <UiChip v-if="e.implemented && e.mode" :tone="e.mode === 'live' ? 'bad' : 'neutral'" :title="e.mode === 'live' ? 'Торговля живыми деньгами' : ''">{{ modeText(e.mode) }}</UiChip>
              </span>
              <span class="mid">
                <span v-if="e.implemented && e.active">{{ e.alive == null ? 'исполнитель: нет данных' : e.alive ? 'исполнитель работает' : 'исполнитель не запущен' }}</span>
                <span v-else-if="!e.implemented" class="muted">торговля на ней пока невозможна</span>
              </span>
              <span class="lim">{{ e.creds?.length ? 'ключи: ' : '' }}{{ credsText(e.creds) }}</span>
              <svg class="chev" viewBox="0 0 12 12" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4.5 3L7.5 6L4.5 9" /></svg>
            </button>
          </li>
        </ul>
        <p v-else class="empty">Биржи не настроены.</p>
      </section>
    </template>
    <p v-else-if="s" class="empty">Настройки провайдеров и бирж не получены от сервера.</p>
    <div v-else class="skel" aria-busy="true" />
  </section>
</template>

<style scoped>
.board { display: grid; gap: var(--space-4); }
.top { display: flex; align-items: baseline; gap: var(--space-3); }
.title { margin: 0; font-size: 16px; font-weight: 700; letter-spacing: -0.01em; }
.sum { padding: var(--space-4) var(--space-5); background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); }
.line { margin: 0; font-size: 16px; font-weight: 600; }
.group { display: grid; gap: var(--space-2); }
.cap { margin: 0; font-size: 12px; font-weight: 600; color: var(--muted); }
.rows { list-style: none; margin: 0; padding: 0; background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); overflow: hidden; }
.rows li + li { border-top: 1px solid var(--line); }
.row { display: grid; grid-template-columns: minmax(0, 1.3fr) minmax(0, 1.1fr) minmax(0, 1fr) 12px; gap: var(--space-4); align-items: start; width: 100%; padding: var(--space-3) var(--space-4); background: none; border: 0; color: inherit; font: inherit; text-align: left; cursor: pointer; transition: background-color var(--dur-fast) ease; }
.row.sel { background: var(--panel2); }
.row:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.who { display: flex; flex-wrap: wrap; align-items: center; gap: 4px var(--space-2); font-size: 14px; }
.what { flex: 1 1 100%; font-size: 12px; color: var(--muted); line-height: 1.4; }
.mid { display: grid; gap: 2px; font-size: 12px; color: var(--muted); min-width: 0; }
.mid .bad { color: var(--neg); }
.note { display: flex; gap: var(--space-2); color: var(--text); }
.note .dot { flex: none; width: 8px; height: 8px; margin-top: 4px; border-radius: 50%; background: var(--warn); }
.note.bad .dot { background: var(--neg); }
.muted { opacity: 0.8; }
.lim { font-size: 12px; color: var(--muted); line-height: 1.4; }
.chev { margin-top: 4px; color: var(--muted); }
.empty { margin: 0; font-size: 13px; color: var(--muted); }
.skel { height: 200px; border-radius: var(--radius); background: var(--panel); }
@media (max-width: 900px) {
  .row { grid-template-columns: minmax(0, 1fr) 12px; }
  .row .mid, .row .lim { grid-column: 1; }
}
@media (hover: hover) and (pointer: fine) {
  .row:not(.sel):hover { background: var(--panel2); }
}
</style>
