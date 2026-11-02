<script setup lang="ts">
import { computed, ref } from 'vue'
import { MAX_FALLBACK, fallbackCandidates, issuesOf, pct, placeOf, profileBlock, roleLabel, useRoleStore } from '@/entities/role'
import { UiChip, UiModal } from '@/shared/ui'
import { fixed, roleAbout } from '@/shared/lib'
import { useRoleEdit } from '../model/useRoleEdit'
import RoleOpts from './RoleOpts.vue'

/**
 * Форма роли в окне: какой профиль думает за роль (основной), какие запасные и в каком порядке, «Дополнительно» (свой лимит расходов и параметры ответа). Выбор копится черновиком,
 * одна кнопка «Применить» запускает общее действие (подтверждение рисков по правилам сервера). После успеха форма показывает «Применено» и ждёт, пока данные отразят изменение, рядом «Вернуть как было».
 * Роль live-команды в live: запасные не меняются, основная идёт через brain.use.
 */
const props = defineProps<{ open: boolean; role: string }>()
const emit = defineEmits<{ close: [] }>()
const store = useRoleStore()
const edit = useRoleEdit(() => props.role)
const r = edit.row
const issues = computed(() => (r.value ? issuesOf(r.value, store.snapshot?.mode ?? null) : []))
const off = computed(() => edit.busy.value || !edit.canEdit.value)
const fb = computed(() => edit.draft.value.fallback)
const candidates = computed(() => fallbackCandidates(edit.profiles.value, edit.draft.value))
const full = computed(() => fb.value.length >= MAX_FALLBACK)
const noList = computed(() => !edit.profiles.value.length)
const label = (id: string): string => { const p = edit.names.value.get(id); return p ? `${p.model_name} · ${placeOf(p)}${profileBlock(p) ? ' (провайдер выключен)' : ''}` : id }
const stateText = (s: 'wait' | 'seen' | 'late'): string => (s === 'seen' ? 'данные обновились' : s === 'late' ? 'данные обновятся скоро' : 'ожидаем обновления данных')
const last = computed(() => edit.entries.value[0] ?? null)
const addValue = ref('')
function addFb(id: string): void {
  if (id) edit.add(id)
  addValue.value = ''
}
const dayLine = computed(() => {
  const d = r.value?.day
  return d ? `Вызовов ${d.calls ?? 0}, ошибок ${pct(d.err_rate)}, ответов не по формату ${pct(d.fmt_err_rate)}, переходов на запасную ${d.fallbacks ?? 0}, стоимость ${fixed(d.usd ?? 0, 4)} USD.` : ''
})
</script>

<template>
  <UiModal :open="open" :label="roleLabel(role)" @close="emit('close')">
    <template #chip>
      <UiChip v-if="r?.live_risk" title="От этой роли зависят решения при торговле на реальные деньги">важна для реальных денег</UiChip>
    </template>

    <template v-if="r">
      <section class="f-sec" aria-labelledby="rf-p">
        <h3 id="rf-p" class="f-h">Основная модель</h3>
        <p class="f-hint">{{ roleAbout(role) }}</p>
        <ul v-if="issues.length" class="issues" aria-label="Замечания"><li v-for="(i, k) in issues" :key="k" :class="i.tone">{{ i.text }}</li></ul>
        <p v-if="!edit.canEdit.value" class="f-note">Менять модели из дашборда пока нельзя: владельцу нужно включить расширенное управление.</p>
        <p v-else-if="noList" class="f-note">Список доступных моделей не пришёл: изменить нельзя. Обновите страницу.</p>
        <div class="f-fld">
          <label for="rf-prim">Профиль</label>
          <select id="rf-prim" class="f-input" :value="edit.draft.value.primary ?? ''" :disabled="off || noList" @change="edit.pickPrimary(($event.target as HTMLSelectElement).value)">
            <option v-if="!edit.draft.value.primary" value="" disabled>не назначен</option>
            <option v-for="p in edit.profiles.value" :key="p.id" :value="p.id" :disabled="!!profileBlock(p)">{{ p.id }}: {{ p.model_name }} · {{ placeOf(p) }}{{ profileBlock(p) ? ' (провайдер выключен)' : '' }}</option>
          </select>
          <p class="f-hint" :class="{ warn: edit.liveRelevant.value && !!r.primary && !r.primary.live_ok }">{{ r.primary ? `Сейчас: ${r.primary.model_name}, ${placeOf(r.primary)}${r.primary.provider_enabled ? '' : ', провайдер выключен'}. ${r.primary.live_ok ? 'Допущена к торговле на реальные деньги.' : 'Не допущена к торговле на реальные деньги.'}` : 'Модель не назначена.' }}</p>
        </div>
      </section>

      <section class="f-sec" aria-labelledby="rf-f">
        <div class="f-hrow">
          <h3 id="rf-f" class="f-h">Запасные модели</h3>
          <span v-if="edit.canEdit.value && !edit.locked.value && !noList" class="count">{{ fb.length }} из {{ MAX_FALLBACK }}</span>
        </div>
        <p class="f-hint">Если основная не ответила, мозг пробует запасные по очереди, сверху вниз.</p>
        <p v-if="edit.locked.value" class="f-note">Пока платформа торгует на реальные деньги, запасные модели этой роли не меняются.</p>

        <TransitionGroup v-if="fb.length" name="feed" tag="ul" class="fbs">
          <li v-for="(id, i) in fb" :key="id" class="fb">
            <span class="fbt"><b>{{ i + 1 }}. {{ label(id) }}</b></span>
            <span v-if="edit.canEdit.value && !edit.locked.value" class="ctl">
              <button type="button" class="ib" :aria-label="`Поднять выше: ${label(id)}`" :disabled="off || i === 0" @click="edit.move(id, -1)">
                <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 14l6-6 6 6" /></svg>
              </button>
              <button type="button" class="ib" :aria-label="`Опустить ниже: ${label(id)}`" :disabled="off || i === fb.length - 1" @click="edit.move(id, 1)">
                <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 10l6 6 6-6" /></svg>
              </button>
              <button type="button" class="ib" :aria-label="`Убрать из запасных: ${label(id)}`" :disabled="off" @click="edit.remove(id)">
                <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>
              </button>
            </span>
          </li>
        </TransitionGroup>
        <p v-else class="f-hint">Запасных нет.</p>

        <div v-if="edit.canEdit.value && !edit.locked.value && !noList" class="f-fld">
          <label for="rf-add">Добавить запасную</label>
          <select id="rf-add" class="f-input" :value="addValue" :disabled="off || full || !candidates.length" @change="addFb(($event.target as HTMLSelectElement).value)">
            <option value="">выберите профиль</option>
            <option v-for="p in candidates" :key="p.id" :value="p.id" :disabled="!!profileBlock(p)">{{ p.id }}: {{ p.model_name }}{{ profileBlock(p) ? ' (провайдер выключен)' : '' }}</option>
          </select>
          <p v-if="full" class="f-hint">Запасных уже {{ MAX_FALLBACK }}: больше нельзя. Добавится в конец цепочки.</p>
          <p v-else class="f-hint">Добавится в конец цепочки.</p>
        </div>

        <p v-if="edit.lines.value.length" class="f-hint">Будет изменено: {{ edit.lines.value.map((l) => `${l.label.toLowerCase()}: ${l.from} → ${l.to}`).join('; ') }}.</p>
        <div v-if="last" class="done">
          <p class="f-hint" :class="{ ok: last.state === 'seen' }" role="status" aria-live="polite"><b>{{ last.kind === 'revert' ? 'Возвращено' : 'Применено' }}</b>: {{ last.lines.map((l) => `${l.label.toLowerCase()} ${l.from} → ${l.to}`).join('; ') }}, {{ stateText(last.state) }}.</p>
          <button v-if="edit.canEdit.value && !last.reverted" type="button" class="f-btn" :disabled="edit.busy.value" @click="edit.revert(last)">Вернуть как было</button>
        </div>
      </section>

      <section class="f-sec">
        <details class="f-more">
          <summary>Дополнительно</summary>
          <RoleOpts :role="r" :target="edit.target.value" />
          <p class="f-hint day">За сутки: {{ dayLine }}</p>
        </details>
      </section>
    </template>
    <p v-else class="f-hint">{{ store.snapshot ? 'Роль не найдена.' : 'Загружаю данные роли…' }}</p>

    <template #footer>
      <button type="button" class="f-ghost" :disabled="edit.busy.value" @click="emit('close')">Закрыть</button>
      <button type="button" class="f-ghost" :disabled="edit.busy.value || !edit.changes.value.any" @click="edit.reset()">Сбросить</button>
      <button type="button" class="f-cta" :disabled="edit.busy.value || !edit.canEdit.value || !edit.changes.value.any" @click="edit.submit()">Применить</button>
    </template>
  </UiModal>
</template>

<style scoped>
.count { font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.issues { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; font-size: 12px; }
.issues li { display: flex; gap: var(--space-2); padding: var(--space-2) var(--space-3); border-radius: var(--radius-sm); background: var(--panel2); color: var(--muted); }
.issues li::before { content: ''; flex: none; width: 8px; height: 8px; margin-top: 5px; border-radius: 50%; background: var(--warn); }
.issues li.bad::before { background: var(--neg); }
.fbs { position: relative; list-style: none; margin: 0; padding: 0; display: grid; gap: var(--space-1); }
.fb { display: flex; align-items: center; justify-content: space-between; gap: var(--space-2); min-height: 40px; }
.fbt { min-width: 0; }
.fbt b { font-size: 13px; font-weight: 500; overflow-wrap: anywhere; }
.ctl { flex: none; display: flex; gap: 2px; }
.ib { display: grid; place-items: center; width: 36px; height: 36px; padding: 0; background: transparent; border: 0; border-radius: var(--radius-sm); color: var(--muted); cursor: pointer; transition: color var(--dur-fast) ease, background-color var(--dur-fast) ease, transform 120ms var(--ease-out); }
.ib:disabled { opacity: 0.45; cursor: not-allowed; }
.ib:active:not(:disabled) { transform: scale(0.95); }
.ib:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.done { display: grid; gap: var(--space-2); justify-items: start; }
.day { margin-top: var(--space-3); }
@media (hover: hover) and (pointer: fine) {
  .ib:hover:not(:disabled) { color: var(--text); background: var(--panel2); }
}
@media (prefers-reduced-motion: reduce) {
  .ib { transition: color var(--dur-fast) ease, background-color var(--dur-fast) ease; }
  .ib:active:not(:disabled) { transform: none; }
}
</style>
