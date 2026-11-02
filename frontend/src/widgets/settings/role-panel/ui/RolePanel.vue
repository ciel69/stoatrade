<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useActionStore } from '@/entities/action'
import { MAX_FALLBACK, fallbackCandidates, issuesOf, pct, placeOf, roleLabel, useRoleStore, type RoleModel } from '@/entities/role'
import { UiChip, UiDetail } from '@/shared/ui'
import { fixed, roleAbout, useRefocus } from '@/shared/lib'
import { useRoleEdit, type AppliedEntry } from '../model/useRoleEdit'
import ProfileList from './ProfileList.vue'

/**
 * Боковая панель роли: что она делает, какая модель основная и какие запасные (где работают, параметры, допуск к live), качество за сутки и расход.
 * Редактирование прямо в панели: «Сменить» основную (выбор из списка профилей), запасные (порядок, убрать, добавить, до четырёх). Выбор копится черновиком со строкой «было → станет»,
 * одна кнопка «Применить» запускает общее действие (подтверждение рисков по правилам сервера). После успеха панель показывает «Применено» и ждёт, пока данные отразят изменение;
 * рядом «Вернуть как было». Роль live-команды в live: запасные не меняются, основная идёт через brain.use. Отладка без сервера: ?edit=mock | mock-calm | mock-live.
 */
const props = defineProps<{ role: string }>()
defineEmits<{ close: [] }>()
const store = useRoleStore()
const action = useActionStore()

const dbg = new URLSearchParams(location.search).get('edit')
if (dbg && dbg.startsWith('mock') && !store.mocked) {
  store.mock(dbg === 'mock-live' ? 'live' : 'edit')
  action.mockEdit(dbg === 'mock-calm')
}

const edit = useRoleEdit(() => props.role)
const r = edit.row
const issues = computed(() => (r.value ? issuesOf(r.value, store.snapshot?.mode ?? null) : []))
const thinking: Record<string, string> = { off: 'выключено', low: 'немного', medium: 'средне', high: 'много' }
const params = (m: RoleModel) => [
  m.params.temperature != null ? `разброс ответов (температура) ${m.params.temperature}` : null,
  m.params.max_out_tok != null ? `ответ до ${m.params.max_out_tok} токенов` : null,
  m.params.thinking ? `рассуждение: ${thinking[m.params.thinking] ?? m.params.thinking}` : null,
].filter(Boolean).join(' · ')
const ms = (x: number | null | undefined) => (x == null ? '–' : x >= 1000 ? `${fixed(x / 1000, 1)} с` : `${x} мс`)

// ---- редактирование
const pickP = ref(false)
const pickAdd = ref(false)
const pBtn = ref<HTMLButtonElement | null>(null)
const aBtn = ref<HTMLButtonElement | null>(null)
const draftFb = computed(() => edit.draft.value.fallback)
const candidates = computed(() => fallbackCandidates(edit.profiles.value, edit.draft.value))
const full = computed(() => draftFb.value.length >= MAX_FALLBACK)
const noList = computed(() => !edit.profiles.value.length)
const off = computed(() => edit.busy.value)
const fbModel = (id: string) => edit.names.value.get(id)

function closeP(back = false): void {
  pickP.value = false
  if (back) nextTick(() => pBtn.value?.focus())
}
function closeAdd(back = false): void {
  pickAdd.value = false
  if (back) nextTick(() => aBtn.value?.focus())
}
const pkP = ref<HTMLElement | null>(null)
const pkA = ref<HTMLElement | null>(null)
/** При открытии выбора фокус уходит на выбранную (или первую доступную) строку: Tab, Enter и Esc работают сразу. */
function focusRow(host: () => HTMLElement | null): void {
  nextTick(() => host()?.querySelector<HTMLElement>('.row.on:not(:disabled), .row:not(:disabled)')?.focus())
}
function togglePrimary(): void {
  pickAdd.value = false
  pickP.value = !pickP.value
  if (pickP.value) focusRow(() => pkP.value)
}
function toggleAdd(): void {
  pickP.value = false
  pickAdd.value = !pickAdd.value
  if (pickAdd.value) focusRow(() => pkA.value)
}
/** Esc закрывает открытый выбор, а не всю панель; без выбора событие идёт дальше и закрывает панель. */
function onEsc(ev: KeyboardEvent): void {
  if (pickP.value) { ev.stopPropagation(); closeP(true) }
  else if (pickAdd.value) { ev.stopPropagation(); closeAdd(true) }
}
function choose(id: string): void {
  edit.pickPrimary(id)
  closeP(true)
}
function addFb(id: string): void {
  edit.add(id)
  closeAdd(true)
}
function apply(): void {
  closeP()
  closeAdd()
  refocus.mark()
  edit.submit()
}
// Панель изменений исчезает вместе с нажатой кнопкой «Применить». Фокус возвращается на заголовок результата «Применено» (его и читает экранный диктор), а если результата нет, на «Сменить» или рамку панели.
// Окно подтверждения рисков вернёт фокус само, поэтому ждём, пока оно закроется.
const detail = ref<{ $el: HTMLElement } | null>(null)
const bar = ref<HTMLElement | null>(null)
const doneBox = ref<HTMLElement | null>(null)
const refocus = useRefocus({
  blocked: () => action.riskyRun === true && action.phase !== 'idle',
  target: () => doneBox.value?.querySelector<HTMLElement>('.dt b') ?? pBtn.value,
  fallback: () => pBtn.value ?? detail.value?.$el,
  lostIn: () => bar.value,
})
watch(() => edit.entries.value[0]?.id, (id) => { if (id) refocus.arm() })
function resetAll(): void {
  closeP()
  closeAdd()
  edit.reset()
  refocus.arm()
}
const stateText = (e: AppliedEntry): string =>
  e.state === 'seen' ? 'данные обновились' : e.state === 'late' ? 'данные обновятся скоро' : 'ожидаем обновления данных'
</script>

<template>
  <UiDetail ref="detail" :label="`Роль ${roleLabel(role)}`" @close="$emit('close')">
    <template #title>
      <b class="h">{{ roleLabel(role) }}</b>
      <UiChip v-if="r?.live_risk" title="От этой роли зависят решения при торговле на реальные деньги">важна для реальных денег</UiChip>
    </template>

    <div v-if="r" class="stack">
      <p class="what">{{ roleAbout(role) }}</p>

      <ul v-if="issues.length" class="issues"><li v-for="(i, k) in issues" :key="k" :class="i.tone">{{ i.text }}</li></ul>

      <section class="sec" @keydown.esc="onEsc">
        <div class="hrow">
          <h4>Основная модель</h4>
          <button v-if="edit.canEdit.value && !noList" ref="pBtn" type="button" class="btn" :aria-expanded="pickP" :disabled="off" @click="togglePrimary">{{ pickP ? 'Закрыть выбор' : 'Сменить' }}</button>
        </div>
        <div v-if="r.primary" class="model">
          <b>{{ r.primary.model_name }}</b>
          <p class="meta">{{ placeOf(r.primary) }} · провайдер {{ r.primary.provider }}{{ r.primary.provider_enabled ? '' : ' (выключен)' }} · {{ r.primary.live_ok ? 'допущена к торговле на реальные деньги' : 'не допущена к торговле на реальные деньги' }}</p>
        </div>
        <p v-else class="meta">Модель не назначена.</p>
        <Transition name="pk">
          <div v-if="pickP" ref="pkP" class="picker">
            <p class="meta">Выберите основную модель. Она применится кнопкой «Применить» ниже.</p>
            <ProfileList :items="edit.profiles.value" :current="edit.base.value.primary" :selected="edit.draft.value.primary" :live-warn="edit.liveRelevant.value" :disabled="off" label="Профили моделей" @pick="choose" />
          </div>
        </Transition>
      </section>

      <section class="sec" @keydown.esc="onEsc">
        <div class="hrow">
          <h4>Запасные модели</h4>
          <span v-if="edit.canEdit.value && !edit.locked.value && !noList" class="count">{{ draftFb.length }} из {{ MAX_FALLBACK }}</span>
        </div>
        <p class="meta">Если основная не ответила, мозг пробует запасные по очереди, сверху вниз.</p>
        <p v-if="edit.locked.value" class="meta lock">Пока платформа торгует на реальные деньги, запасные модели этой роли не меняются.</p>

        <!-- текущие запасные -->
        <TransitionGroup v-if="edit.canEdit.value && !edit.locked.value" name="feed" tag="ul" class="fbs">
          <li v-for="(id, i) in draftFb" :key="id" class="fb">
            <div class="fbt">
              <b>{{ i + 1 }}. {{ fbModel(id)?.model_name ?? id }}</b>
              <p class="meta" :class="{ warn: fbModel(id) && !fbModel(id)!.provider_enabled }">{{ fbModel(id) ? `${placeOf(fbModel(id)!)} · провайдер ${fbModel(id)!.provider}${fbModel(id)!.provider_enabled ? '' : ' выключен: запасная не сработает'}` : 'эта модель не найдена в списке доступных' }}</p>
            </div>
            <div class="ctl">
              <button type="button" class="ib" :aria-label="`Поднять выше: ${fbModel(id)?.model_name ?? id}`" :disabled="off || i === 0" @click="edit.move(id, -1)">
                <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 14l6-6 6 6" /></svg>
              </button>
              <button type="button" class="ib" :aria-label="`Опустить ниже: ${fbModel(id)?.model_name ?? id}`" :disabled="off || i === draftFb.length - 1" @click="edit.move(id, 1)">
                <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 10l6 6 6-6" /></svg>
              </button>
              <button type="button" class="ib" :aria-label="`Убрать из запасных: ${fbModel(id)?.model_name ?? id}`" :disabled="off" @click="edit.remove(id)">
                <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>
              </button>
            </div>
          </li>
        </TransitionGroup>
        <ul v-else-if="r.fallback.length" class="fbs">
          <li v-for="(f, i) in r.fallback" :key="f.id" class="fb">
            <div class="fbt">
              <b>{{ i + 1 }}. {{ f.model_name }}</b>
              <p class="meta" :class="{ warn: !f.provider_enabled }">{{ placeOf(f) }} · провайдер {{ f.provider }}{{ f.provider_enabled ? '' : ' выключен: запасная не сработает' }}</p>
              <p class="meta">{{ params(f) }}</p>
            </div>
          </li>
        </ul>
        <p v-if="!draftFb.length && edit.canEdit.value && !edit.locked.value" class="meta">Запасных нет.</p>
        <p v-else-if="!r.fallback.length && !(edit.canEdit.value && !edit.locked.value)" class="meta">Запасных нет.</p>

        <template v-if="edit.canEdit.value && !edit.locked.value && !noList">
          <button ref="aBtn" type="button" class="btn add" :aria-expanded="pickAdd" :disabled="off || full || !candidates.length" @click="toggleAdd">{{ pickAdd ? 'Закрыть выбор' : 'Добавить запасную' }}</button>
          <p v-if="full" class="meta">Запасных уже {{ MAX_FALLBACK }}: больше нельзя.</p>
          <Transition name="pk">
            <div v-if="pickAdd" ref="pkA" class="picker">
              <p class="meta">Добавится в конец цепочки.</p>
              <ProfileList :items="candidates" :live-warn="false" :disabled="off" label="Профили для запасной" @pick="addFb" />
            </div>
          </Transition>
        </template>
      </section>

      <p v-if="!edit.canEdit.value" class="locked">Менять модели из дашборда пока нельзя: владельцу нужно включить расширенное управление.</p>
      <p v-else-if="noList" class="locked">Список доступных моделей не пришёл: изменить нельзя. Обновите страницу.</p>

      <section v-if="edit.entries.value.length" ref="doneBox" class="sec">
        <h4>Применено в этой сессии</h4>
        <TransitionGroup name="feed" tag="ul" class="done">
          <li v-for="e in edit.entries.value" :key="e.id" class="dn">
            <svg v-if="e.state === 'wait'" class="spin-w" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><circle cx="8" cy="8" r="6" fill="none" stroke="var(--line2)" stroke-width="2" /><path class="arc" d="M8 2a6 6 0 0 1 6 6" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linecap="round" /></svg>
            <svg v-else class="okc" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5" /></svg>
            <div class="dt">
              <b tabindex="-1">{{ e.kind === 'revert' ? 'Возвращено' : 'Применено' }}</b>
              <p v-for="l in e.lines" :key="l.label" class="ln">{{ l.label }}: {{ l.from }} <span class="ar">→</span> {{ l.to }}</p>
              <p class="meta" role="status" aria-live="polite">{{ stateText(e) }}</p>
            </div>
            <button v-if="edit.canEdit.value && !e.reverted" type="button" class="btn" :disabled="off" @click="edit.revert(e)">Вернуть как было</button>
            <span v-else-if="e.reverted" class="meta">откатано</span>
          </li>
        </TransitionGroup>
      </section>

      <!-- сразу под редактируемыми блоками; при длинном содержимом липнет к низу панели -->
      <Transition name="bar">
        <div v-if="edit.changes.value.any" ref="bar" class="chbar" role="group" aria-label="Изменения моделей роли">
          <dl class="chg">
            <div v-for="l in edit.lines.value" :key="l.label"><dt>{{ l.label }}</dt><dd>{{ l.from }} <span class="ar">→</span> <b>{{ l.to }}</b></dd></div>
          </dl>
          <div class="acts">
            <button type="button" class="ghost" :disabled="off" @click="resetAll">Сбросить</button>
            <button type="button" class="cta" :disabled="off" @click="apply">Применить</button>
          </div>
        </div>
      </Transition>

      <section class="sec">
        <h4>Как справлялась за сутки</h4>
        <dl class="kv">
          <div><dt>Вызовов</dt><dd>{{ r.day.calls ?? 0 }}</dd></div>
          <div><dt>Ошибки</dt><dd>{{ pct(r.day.err_rate) }}</dd></div>
          <div><dt>Ответов с неверным форматом</dt><dd>{{ pct(r.day.fmt_err_rate) }}</dd></div>
          <div><dt>Переходов на запасную модель</dt><dd>{{ r.day.fallbacks ?? 0 }}</dd></div>
          <div><dt>Обычное время ответа</dt><dd>{{ ms(r.day.p50_ms) }}</dd></div>
          <div><dt>Стоимость</dt><dd>{{ fixed(r.day.usd ?? 0, 4) }} USD</dd></div>
          <div v-if="r.budget_day_usd != null"><dt>Лимит расходов в день</dt><dd>{{ fixed(r.budget_day_usd, 2) }} USD, осталось {{ fixed(r.today.budget_left_usd, 2) }}</dd></div>
        </dl>
      </section>

      <details class="tech">
        <summary>Для специалиста</summary>
        <p v-if="r.primary">Параметры модели: {{ params(r.primary) || 'по умолчанию' }}</p>
        <p v-for="f in r.fallback" :key="f.id">Запасная {{ f.model_name }}: {{ params(f) || 'по умолчанию' }}</p>
        <p>профиль {{ r.primary?.id ?? '–' }} · роль {{ r.role }} · {{ edit.target.value }} · {{ r.scope }}{{ r.team ? ' ' + r.team : '' }} · {{ r.io ?? '' }}</p>
      </details>
    </div>
    <p v-else class="meta">{{ store.snapshot ? 'Роль не найдена.' : 'Загружаю данные роли…' }}</p>
  </UiDetail>
</template>

<style scoped>
.h { font-size: 17px; }
.stack { display: grid; gap: var(--space-4); align-content: start; }
.what { margin: 0; font-size: 13px; line-height: 1.5; }
.issues { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; font-size: 12px; }
.issues li { display: flex; gap: var(--space-2); padding: var(--space-2) var(--space-3); border-radius: var(--radius-sm); background: var(--panel2); color: var(--muted); }
.issues li::before { content: ''; flex: none; width: 8px; height: 8px; margin-top: 5px; border-radius: 50%; background: var(--warn); }
.issues li.bad::before { background: var(--neg); }
.sec { display: grid; gap: var(--space-2); }
.sec h4 { margin: 0; font-size: 12px; font-weight: 600; color: var(--muted); }
.hrow { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); min-height: 36px; }
.count { font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.model b, .fbt b { font-size: 14px; overflow-wrap: anywhere; }
.meta { margin: 2px 0 0; font-size: 12px; color: var(--muted); line-height: 1.5; }
.meta.warn { color: var(--warn); }
.meta.lock { padding: var(--space-2) var(--space-3); border-radius: var(--radius-sm); background: var(--panel2); }
.fbs, .done { position: relative; list-style: none; margin: 0; padding: 0; display: grid; gap: var(--space-3); }
.fbs:empty { display: none; }
.fb { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--space-2); }
.fbt { min-width: 0; }
.ctl { flex: none; display: flex; gap: 2px; margin: -4px -6px 0 0; }
.kv { margin: 0; display: grid; gap: 6px; font-size: 12px; }
.kv > div { display: flex; justify-content: space-between; gap: var(--space-4); }
dt { color: var(--muted); }
dd { margin: 0; text-align: right; font-variant-numeric: tabular-nums; }
.locked { margin: 0; padding: var(--space-3); border-radius: var(--radius-sm); background: var(--panel2); font-size: 12px; color: var(--muted); line-height: 1.5; }
.tech { font-size: 12px; color: var(--muted); }
.tech summary { cursor: pointer; min-height: 32px; display: flex; align-items: center; }
.tech summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 2px; }
.tech p { margin: 4px 0 0; font-size: 11px; font-family: var(--mono, ui-monospace, monospace); overflow-wrap: anywhere; }

.picker { display: grid; gap: var(--space-2); padding-top: var(--space-1); }

.btn, .ghost, .cta { min-height: 36px; padding: 0 var(--space-3); font: inherit; font-size: 12px; border-radius: var(--radius-sm); cursor: pointer; white-space: nowrap; transition: color var(--dur-fast) ease, border-color var(--dur-fast) ease, opacity var(--dur-fast) ease, filter var(--dur-fast) ease, transform 120ms var(--ease-out); }
.btn, .ghost { background: transparent; color: var(--text); border: 1px solid var(--line2); }
.btn.add { justify-self: start; }
.cta { background: var(--accent); color: var(--bg); border: 0; font-weight: 650; font-size: 13px; padding: 0 var(--space-4); min-height: 40px; }
.ghost { color: var(--muted); font-size: 13px; min-height: 40px; padding: 0 var(--space-4); }
.btn:disabled, .ghost:disabled, .cta:disabled, .ib:disabled { opacity: 0.45; cursor: not-allowed; }
.btn:active:not(:disabled), .ghost:active:not(:disabled), .cta:active:not(:disabled), .ib:active:not(:disabled) { transform: scale(0.97); }
.ib { display: grid; place-items: center; width: 32px; height: 32px; padding: 0; background: transparent; border: 0; border-radius: var(--radius-sm); color: var(--muted); cursor: pointer; transition: color var(--dur-fast) ease, background-color var(--dur-fast) ease, transform 120ms var(--ease-out); }
.btn:focus-visible, .ghost:focus-visible, .cta:focus-visible, .ib:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }

.dn { display: grid; grid-template-columns: 16px minmax(0, 1fr) auto; align-items: start; gap: var(--space-2); }
.dn > svg { margin-top: 2px; }
.okc { color: var(--pos); }
.dt b { font-size: 13px; }
.dt b:focus { outline: none; }
.dt b:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 2px; }
.ln { margin: 2px 0 0; font-size: 12px; overflow-wrap: anywhere; }
.ar { color: var(--muted); }
.spin-w .arc { transform-origin: 8px 8px; animation: rp-spin 0.9s linear infinite; }
@keyframes rp-spin { to { transform: rotate(360deg); } }

/* Панель изменений липнет к низу панели: «Применить» всегда под рукой, даже если выбрана модель вверху. */
.chbar { position: sticky; bottom: calc(-1 * var(--space-4)); z-index: 2; display: grid; gap: var(--space-3); margin: 0 calc(-1 * var(--space-4)); padding: var(--space-3) var(--space-4); background: var(--panel); border-top: 1px solid var(--line2); }
.chg { margin: 0; display: grid; gap: 4px; font-size: 12px; }
.chg > div { display: grid; grid-template-columns: 70px minmax(0, 1fr); gap: var(--space-2); }
.chg dd { text-align: left; overflow-wrap: anywhere; }
.chg b { font-weight: 650; }
.acts { display: flex; justify-content: flex-end; gap: var(--space-2); }

/* появление: короткое, только прозрачность и сдвиг */
.pk-enter-from { opacity: 0; transform: translateY(-4px); }
.pk-enter-active { transition: opacity var(--dur-enter) var(--ease-out), transform var(--dur-enter) var(--ease-out); }
.pk-leave-to { opacity: 0; }
.pk-leave-active { transition: opacity 120ms var(--ease-out); }
.bar-enter-from { opacity: 0; transform: translateY(6px); }
.bar-enter-active { transition: opacity var(--dur-enter) var(--ease-out), transform var(--dur-enter) var(--ease-out); }
.bar-leave-to { opacity: 0; }
.bar-leave-active { transition: opacity 120ms var(--ease-out); }

@media (hover: hover) and (pointer: fine) {
  .btn:hover:not(:disabled), .ghost:hover:not(:disabled) { border-color: var(--muted); color: var(--text); }
  .cta:hover:not(:disabled) { filter: brightness(1.08); }
  .ib:hover:not(:disabled) { color: var(--text); background: var(--panel2); }
}
@media (prefers-reduced-motion: reduce) {
  .pk-enter-from, .bar-enter-from { transform: none; }
  .btn, .ghost, .cta, .ib { transition: color var(--dur-fast) ease, border-color var(--dur-fast) ease, opacity var(--dur-fast) ease; }
  .spin-w .arc { animation: none; }
}
</style>
