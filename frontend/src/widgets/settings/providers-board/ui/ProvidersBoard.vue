<script setup lang="ts">
import { computed, ref } from 'vue'
import { credText, limitsText, modelsText, providerIssues, useRoleStore } from '@/entities/role'
import { UiChip } from '@/shared/ui'
import ProviderForm from './ProviderForm.vue'

/**
 * Список «Провайдеры»: кто запускает модели мозга. В строке название, включён ли, где работает, сколько моделей, состояние ключа, ограничения расхода и замечания.
 * «Изменить» у строки и «Добавить» над списком открывают одну и ту же форму в окне (ProviderForm). Ключи не показываются вообще, только факт «ключ есть».
 */
const store = useRoleStore()
const s = computed(() => store.snapshot)
const list = computed(() => s.value?.provider_list ?? [])
const open = ref(false)
const editId = ref<string | null>(null)
const intro = ref('')
/** новый экземпляр формы на каждое открытие: черновик не тянется из прошлого раза */
const session = ref(0)

function openNew(): void {
  editId.value = null
  intro.value = ''
  session.value += 1
  open.value = true
}
function openEdit(id: string): void {
  editId.value = id
  intro.value = ''
  session.value += 1
  open.value = true
}
function created(id: string): void {
  editId.value = id
  intro.value = 'Провайдер создан: он выключен и без ключа. Введите ключ, добавьте модели и включите его.'
}
const issues = (p: (typeof list.value)[number]) => providerIssues(p, s.value?.roles ?? [])
</script>

<template>
  <section class="board" aria-label="Провайдеры">
    <header class="top">
      <div>
        <h2 class="title">Провайдеры</h2>
        <p class="sub">Сервисы, на которых работают модели: облако или ваш сервер.</p>
      </div>
      <button type="button" class="f-btn" :disabled="!s" @click="openNew">Добавить провайдера</button>
    </header>

    <ul v-if="list.length" class="rows">
      <li v-for="p in list" :key="p.id" class="row">
        <div class="txt">
          <span class="who"><b>{{ p.name }}</b><UiChip :tone="p.enabled ? 'ok' : 'neutral'">{{ p.enabled ? 'включён' : 'выключен' }}</UiChip></span>
          <span class="meta"><span>{{ p.local ? 'на этом компьютере' : 'в облаке' }}</span> · {{ modelsText(p.models.length) }} · <span :class="{ bad: p.cred.needed && !p.cred.present && p.enabled }">{{ credText({ cred: p.cred, local: p.local }) }}</span> · {{ limitsText(p.limits) }}</span>
          <span v-for="(i, k) in issues(p)" :key="k" class="note" :class="i.tone"><span class="dot" aria-hidden="true" />{{ i.text }}</span>
        </div>
        <button type="button" class="f-btn" :aria-label="`Изменить провайдера ${p.name}`" @click="openEdit(p.id)">Изменить</button>
      </li>
    </ul>
    <p v-else-if="s" class="empty">Провайдеров пока нет. Добавьте первого, чтобы моделям было где работать.</p>
    <div v-else class="skel" aria-busy="true" />

    <ProviderForm :key="session" :open="open" :provider-id="editId" :intro="intro" @close="open = false" @created="created" />
  </section>
</template>

<style scoped>
.board { display: grid; gap: var(--space-3); }
.top { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--space-3); }
.top .f-btn { flex: none; }
@media (max-width: 560px) { .top { flex-wrap: wrap; } }
.title { margin: 0 0 2px; font-size: 16px; font-weight: 700; letter-spacing: -0.01em; }
.sub { margin: 0; font-size: 12px; color: var(--muted); }
.rows { list-style: none; margin: 0; padding: 0; background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); }
.row { display: flex; align-items: center; justify-content: space-between; gap: var(--space-4); padding: var(--space-3) var(--space-4); }
.row + .row { border-top: 1px solid var(--line); }
.txt { min-width: 0; display: grid; gap: 3px; }
.who { display: flex; flex-wrap: wrap; align-items: center; gap: 4px var(--space-2); font-size: 14px; }
.who b { overflow-wrap: anywhere; }
.meta { font-size: 12px; color: var(--muted); line-height: 1.45; }
.meta .bad { color: var(--neg); }
.note { display: flex; gap: var(--space-2); font-size: 12px; }
.note .dot { flex: none; width: 8px; height: 8px; margin-top: 4px; border-radius: 50%; background: var(--warn); }
.note.bad .dot { background: var(--neg); }
.empty { margin: 0; font-size: 13px; color: var(--muted); }
.skel { height: 120px; border-radius: var(--radius); background: var(--panel); }
.row .f-btn { flex: none; }
@media (max-width: 560px) {
  .row { gap: var(--space-3); padding: var(--space-3); }
}
</style>
