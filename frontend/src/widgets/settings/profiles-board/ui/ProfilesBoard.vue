<script setup lang="ts">
import { computed, ref } from 'vue'
import { paramsText, useRoleStore } from '@/entities/role'
import ProfileForm from './ProfileForm.vue'

/**
 * Список «Профили моделей»: модель с настройками ответа, из которых роли выбирают себе основную и запасные. В строке название, модель и провайдер, настройки словами, сколько ролей используют.
 * «Изменить» у строки и «Добавить» над списком открывают одну и ту же форму в окне (ProfileForm).
 */
const store = useRoleStore()
const s = computed(() => store.snapshot)
const list = computed(() => s.value?.profile_list ?? [])
const provName = (id: string): string => s.value?.provider_list?.find((p) => p.id === id)?.name ?? id
const open = ref(false)
const editId = ref<string | null>(null)
const session = ref(0)
function openNew(): void { editId.value = null; session.value += 1; open.value = true }
function openEdit(id: string): void { editId.value = id; session.value += 1; open.value = true }
const used = (u?: string[]): string => (u && u.length ? `используют ролей: ${u.length}` : 'пока никто не использует')
</script>

<template>
  <section class="board" aria-label="Профили моделей">
    <header class="top">
      <div>
        <h2 class="title">Профили моделей</h2>
        <p class="sub">Модель с настройками ответа. Роли выбирают профили из этого списка.</p>
      </div>
      <button type="button" class="f-btn" :disabled="!s" @click="openNew">Добавить профиль</button>
    </header>

    <ul v-if="list.length" class="rows">
      <li v-for="p in list" :key="p.id" class="row">
        <div class="txt">
          <b>{{ p.id }}</b>
          <span class="meta">{{ p.model_name }} · {{ provName(p.provider) }}{{ p.provider_enabled ? '' : ' (выключен)' }}</span>
          <span class="meta">{{ paramsText(p.params) }} · {{ used(p.used_by) }}</span>
        </div>
        <button type="button" class="f-btn" :aria-label="`Изменить профиль ${p.id}`" @click="openEdit(p.id)">Изменить</button>
      </li>
    </ul>
    <p v-else-if="s" class="empty">Профилей пока нет.</p>
    <div v-else class="skel" aria-busy="true" />

    <ProfileForm :key="session" :open="open" :profile-id="editId" @close="open = false" />
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
.txt { min-width: 0; display: grid; gap: 2px; }
.txt b { font-size: 14px; overflow-wrap: anywhere; }
.meta { font-size: 12px; color: var(--muted); line-height: 1.45; }
.empty { margin: 0; font-size: 13px; color: var(--muted); }
.skel { height: 120px; border-radius: var(--radius); background: var(--panel); }
.row .f-btn { flex: none; }
@media (max-width: 560px) {
  .row { gap: var(--space-3); padding: var(--space-3); }
}
</style>
