<script setup lang="ts">
import { computed, ref } from 'vue'
import { credsText, exchangeStatus, modeText, plural, useRoleStore } from '@/entities/role'
import { UiChip, UiModal } from '@/shared/ui'
import { useExchangeEdit } from '../model/useExchangeEdit'
import ExchangeEdit from './ExchangeEdit.vue'

/**
 * Блок «Биржа»: где торгует платформа. В строке название, статус, режим и жив ли исполнитель сделок; «Подробнее» открывает окно: что сейчас, ключи доступа (только виды, не значения),
 * лимит позиций и правка (сделать основной, режим биржи; только при остановленной платформе). Биржи без поддержки не показываются списком, только числом. Отладка без сервера: ?edit=mock.
 */
const store = useRoleStore()
const s = computed(() => store.snapshot)
const ready = computed(() => (s.value?.exchange_list ?? []).filter((e) => e.implemented))
const waiting = computed(() => (s.value?.exchange_list ?? []).filter((e) => !e.implemented).length)
const open = ref(false)
const sel = ref('')
const session = ref(0)
function show(id: string): void {
  sel.value = id
  session.value += 1
  open.value = true
}
const xedit = useExchangeEdit(() => sel.value)
const e = computed(() => ready.value.find((x) => x.id === sel.value) ?? null)
</script>

<template>
  <section class="board" aria-label="Биржа">
    <header class="top">
      <h2 class="title">Биржа</h2>
      <p class="sub">Где торгует платформа и в каком режиме.</p>
    </header>

    <ul v-if="ready.length" class="rows">
      <li v-for="x in ready" :key="x.id" class="row">
        <div class="txt">
          <span class="who">
            <b>{{ x.name }}</b>
            <UiChip :tone="x.active ? 'accent' : 'neutral'">{{ exchangeStatus(x) }}</UiChip>
            <UiChip v-if="x.mode" :tone="x.mode === 'live' ? 'bad' : 'neutral'" :title="x.mode === 'live' ? 'Торговля на реальные деньги' : ''">{{ modeText(x.mode) }}</UiChip>
          </span>
          <span v-if="x.active" class="meta">{{ x.alive == null ? 'нет данных об исполнителе' : x.alive ? 'исполнитель сделок работает' : 'исполнитель сделок не запущен' }}</span>
        </div>
        <button type="button" class="f-btn" :aria-label="`Подробнее о бирже ${x.name}`" @click="show(x.id)">Подробнее</button>
      </li>
    </ul>
    <p v-else-if="s" class="empty">Подходящих бирж нет.</p>
    <div v-else class="skel" aria-busy="true" />
    <p v-if="waiting" class="meta">Ещё {{ waiting }} {{ plural(waiting, 'биржа', 'биржи', 'бирж') }} пока не {{ waiting === 1 ? 'поддерживается' : 'поддерживаются' }}: торговать на них нельзя.</p>

    <UiModal :key="session" :open="open" :label="`Биржа ${e?.name ?? ''}`" @close="open = false">
      <template v-if="e">
        <section class="f-sec">
          <h3 class="f-h">Сейчас</h3>
          <p class="f-hint">{{ e.active ? 'На этой бирже сейчас торгует платформа.' : e.allowed ? 'Биржа разрешена владельцем, но сейчас не активна.' : 'Биржа не разрешена владельцем.' }}</p>
          <dl class="kv">
            <div><dt>Режим</dt><dd><UiChip :tone="e.mode === 'live' ? 'bad' : 'neutral'">{{ modeText(e.mode) }}</UiChip></dd></div>
            <div><dt>Исполнитель сделок</dt><dd>{{ e.alive == null ? 'нет данных' : e.alive ? 'работает' : 'не запущен' }}</dd></div>
            <div><dt>Ключи доступа</dt><dd>{{ credsText(e.creds) }}</dd></div>
            <div><dt>Лимит открытых позиций</dt><dd>{{ e.max_open_positions ?? '–' }}</dd></div>
          </dl>
          <p v-if="e.note" class="f-hint">{{ e.note }}</p>
        </section>
        <section class="f-sec">
          <ExchangeEdit v-if="xedit.canEdit.value" :edit="xedit" />
          <p v-else class="f-note">Менять биржи из дашборда пока нельзя: владельцу нужно включить расширенное управление.</p>
        </section>
      </template>
      <template #footer>
        <button type="button" class="f-ghost" @click="open = false">Закрыть</button>
      </template>
    </UiModal>
  </section>
</template>

<style scoped>
.board { display: grid; gap: var(--space-3); }
.top { display: grid; gap: 2px; }
.title { margin: 0 0 2px; font-size: 16px; font-weight: 700; letter-spacing: -0.01em; }
.sub, .meta { margin: 0; font-size: 12px; color: var(--muted); line-height: 1.45; }
.rows { list-style: none; margin: 0; padding: 0; background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); }
.row { display: flex; align-items: center; justify-content: space-between; gap: var(--space-4); padding: var(--space-3) var(--space-4); }
.row + .row { border-top: 1px solid var(--line); }
.txt { min-width: 0; display: grid; gap: 3px; }
.who { display: flex; flex-wrap: wrap; align-items: center; gap: 4px var(--space-2); font-size: 14px; }
.empty { margin: 0; font-size: 13px; color: var(--muted); }
.skel { height: 80px; border-radius: var(--radius); background: var(--panel); }
.kv { margin: 0; display: grid; gap: 6px; font-size: 12px; }
.kv > div { display: flex; justify-content: space-between; gap: var(--space-4); }
dt { color: var(--muted); }
dd { margin: 0; text-align: right; }
.row .f-btn { flex: none; }
@media (max-width: 560px) {
  .row { gap: var(--space-3); padding: var(--space-3); }
}
</style>
