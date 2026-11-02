<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useActionStore } from '@/entities/action'
import { categoryName, emptyNote, quietHint, quietNow, statsText, useNotifyStore } from '@/entities/notify'
import { UiChangeBar, UiSwitch } from '@/shared/ui'
import { reflectText, useRefocus } from '@/shared/lib'
import { useNotifyEdit } from '../model/useNotifyEdit'

/**
 * Страница «Система · Уведомления»: главный переключатель и пробное уведомление, «О чём сообщать» (важное и остальное), «Тихие часы»; редкое (часовой пояс, антиспам,
 * «показывать и в тихие часы», статистика, подсказка ОС) свёрнуто в «Дополнительно». Правка идёт черновиком: «было → станет», «Применить» и «Отменить» (действие notify.set безрисковое,
 * диалога рисков нет). Пока тема не отразила применённое, показывается «Ожидаем обновления данных». Без расширенного управления остаётся чтение и заметка.
 * Отладка без сервера: ?notify=mock, ?notify=mock-quiet (тихие часы сейчас), ?notify=mock-empty (своих настроек ещё нет).
 */
const store = useNotifyStore()
const action = useActionStore()
const dbg = new URLSearchParams(location.search).get('notify')
onMounted(() => {
  store.start()
  if (dbg && dbg.startsWith('mock')) action.mockNotify()
})
const e = useNotifyEdit()

const snap = e.snap
const d = e.draft
const off = computed(() => !e.canEdit.value || e.busy.value)
const important = computed(() => (snap.value?.categories ?? []).filter((c) => c.critical))
const rest = computed(() => (snap.value?.categories ?? []).filter((c) => !c.critical))
const allCats = computed(() => snap.value?.categories ?? [])
const line = computed(() => (d.value?.master ? 'Уведомления включены.' : 'Уведомления выключены.'))
const quiet = computed(() => quietNow(snap.value?.quiet_now ?? false, snap.value?.held_n ?? 0))
const hint = computed(() => (d.value ? quietHint(d.value.quiet.from, d.value.quiet.to) : ''))
const changeLine = computed(() => e.lines.value.map((l) => `${l.label}: ${l.from} → ${l.to}`).join('\n'))
const last = computed(() => (snap.value?.sent_last ? categoryName(snap.value.sent_last.cat, allCats.value.find((c) => c.id === snap.value?.sent_last?.cat)?.label) : ''))
const stats = computed(() => (snap.value ? statsText(snap.value.stats).join(', ') + (last.value ? `. Последнее: ${last.value}.` : '.') : ''))

function setCat(id: string, v: boolean): void {
  if (d.value) d.value.categories[id] = v
}
function setBypass(id: string, v: boolean): void {
  if (!d.value) return
  const has = d.value.quiet.bypass.includes(id)
  if (v && !has) d.value.quiet.bypass = [...d.value.quiet.bypass, id]
  else if (!v && has) d.value.quiet.bypass = d.value.quiet.bypass.filter((x) => x !== id)
}
function setInt(key: 'per_hour' | 'same_kind_min', raw: string): void {
  if (!d.value) return
  const t = raw.trim()
  d.value.limits[key] = /^\d+$/.test(t) ? Number(t) : null
}
const limitText = (v: number | null | undefined): string => (v == null ? '' : String(v))

// фокус не теряется, когда панель изменений исчезает вместе с нажатой кнопкой; окно подтверждения не бывает (безрисковое), но проверка единообразная с другими правками
const bar = ref<{ $el: HTMLElement } | null>(null)
const master = ref<HTMLElement | null>(null)
const refocus = useRefocus({
  blocked: () => action.riskyRun === true && action.phase !== 'idle',
  target: () => master.value?.querySelector<HTMLElement>('button[role="switch"]'),
  lostIn: () => bar.value?.$el,
})
watch(e.dirty, (v, o) => { if (!v && o) refocus.arm() })
function apply(): void { refocus.mark(); e.submit() }
function cancel(): void { refocus.mark(); e.reset() }
</script>

<template>
  <section class="board" aria-label="Уведомления">
    <header class="top">
      <h2 class="title">Уведомления на компьютере</h2>
      <p v-if="d" class="line">{{ line }}</p>
    </header>

    <template v-if="snap && d">
      <p v-if="snap.source === 'config'" class="note">{{ emptyNote(snap.settings.master) }}</p>
      <p v-if="snap.warn" class="note warn" role="alert">Файл настроек уведомлений не удалось прочитать, поэтому всё выключено. Сохраните настройки заново.</p>
      <p v-if="!e.canEdit.value" class="note">Менять уведомления из дашборда пока нельзя: владельцу нужно включить расширенное управление. Здесь только чтение.</p>

      <div ref="master" class="rows">
        <div class="row">
          <div class="txt">
            <b id="n-master-l">Показывать уведомления</b>
            <p id="n-master-d" class="sub">Короткие сообщения системы о важном. Тексты только из шаблонов, без ключей и личных данных.</p>
          </div>
          <UiSwitch v-model="d.master" label-id="n-master-l" describedby="n-master-d" :disabled="off" />
        </div>
        <div class="row">
          <div class="txt">
            <b id="n-test-l">Проверить, как это выглядит</b>
            <p id="n-test-d" class="sub">Покажет одно настоящее уведомление на экране.</p>
          </div>
          <button type="button" class="btn" aria-describedby="n-test-d" :disabled="!e.canTest.value || e.busy.value" @click="e.test()">Показать пробное</button>
        </div>
      </div>
      <p v-if="snap.hint" class="sub small">Если пробное не появилось: {{ snap.hint.replace(/^Если уведомление не показывается:\s*/i, '') }}</p>
      <p v-if="quiet" class="note" role="status">{{ quiet }}</p>

      <section class="block" aria-labelledby="n-cats-h">
        <h3 id="n-cats-h" class="h">О чём сообщать</h3>
        <p v-if="!d.master" class="sub">Пока уведомления выключены, ничего не покажется. Выберите, о чём сообщать, и включите их выше.</p>
        <h4 v-if="important.length" class="cap">Важное</h4>
        <ul v-if="important.length" class="rows" aria-label="Важные уведомления">
          <li v-for="c in important" :key="c.id" class="row">
            <b :id="'n-c-' + c.id">{{ categoryName(c.id, c.label) }}</b>
            <UiSwitch :model-value="d.categories[c.id] ?? false" :label-id="'n-c-' + c.id" :disabled="off" @update:model-value="setCat(c.id, $event)" />
          </li>
        </ul>
        <h4 v-if="rest.length" class="cap">Остальное</h4>
        <ul v-if="rest.length" class="rows" aria-label="Остальные уведомления">
          <li v-for="c in rest" :key="c.id" class="row">
            <b :id="'n-c-' + c.id">{{ categoryName(c.id, c.label) }}</b>
            <UiSwitch :model-value="d.categories[c.id] ?? false" :label-id="'n-c-' + c.id" :disabled="off" @update:model-value="setCat(c.id, $event)" />
          </li>
        </ul>
      </section>

      <section class="block" aria-labelledby="n-quiet-h">
        <h3 id="n-quiet-h" class="h">Тихие часы</h3>
        <div class="rows">
          <div class="row">
            <div class="txt">
              <b id="n-q-l">Не беспокоить в заданное время</b>
              <p id="n-q-d" class="sub">Уведомления копятся и приходят одним сообщением. Ночью это удобно: можно указать, например, с 23:00 до 07:00.</p>
            </div>
            <UiSwitch v-model="d.quiet.enabled" label-id="n-q-l" describedby="n-q-d" :disabled="off" />
          </div>
        </div>
        <Transition name="reveal">
          <div v-if="d.quiet.enabled" class="times">
            <div class="fld">
              <label for="n-from">С</label>
              <input id="n-from" v-model="d.quiet.from" class="inp" type="time" :disabled="off" :aria-invalid="!!e.errors.value.from" aria-describedby="n-time-h">
            </div>
            <div class="fld">
              <label for="n-to">До</label>
              <input id="n-to" v-model="d.quiet.to" class="inp" type="time" :disabled="off" :aria-invalid="!!e.errors.value.to" aria-describedby="n-time-h">
            </div>
            <p id="n-time-h" class="sub grow" :class="{ bad: !!(e.errors.value.from || e.errors.value.to) }">{{ e.errors.value.from ?? e.errors.value.to ?? hint }}</p>
          </div>
        </Transition>
      </section>

      <details class="adv">
        <summary>Дополнительно</summary>
        <div class="adv-in">
          <div class="fld wide">
            <label for="n-tz">Часовой пояс тихих часов</label>
            <select id="n-tz" v-model="d.quiet.tz" class="inp" :disabled="off">
              <option value="local">местное время</option>
              <option value="utc">UTC (всемирное время)</option>
            </select>
          </div>

          <fieldset class="grp" :disabled="off">
            <legend>Антиспам</legend>
            <div class="pair">
              <div class="fld">
                <label for="n-ph">Не больше в час на тему</label>
                <input id="n-ph" class="inp" type="text" inputmode="numeric" autocomplete="off" :value="limitText(d.limits.per_hour)" :aria-invalid="!!e.errors.value.per_hour" aria-describedby="n-ph-h" @input="setInt('per_hour', ($event.target as HTMLInputElement).value)">
                <p id="n-ph-h" class="sub" :class="{ bad: !!e.errors.value.per_hour }">{{ e.errors.value.per_hour ?? 'От 1 до 60.' }}</p>
              </div>
              <div class="fld">
                <label for="n-sk">Один и тот же вид не чаще, минут</label>
                <input id="n-sk" class="inp" type="text" inputmode="numeric" autocomplete="off" :value="limitText(d.limits.same_kind_min)" :aria-invalid="!!e.errors.value.same_kind_min" aria-describedby="n-sk-h" @input="setInt('same_kind_min', ($event.target as HTMLInputElement).value)">
                <p id="n-sk-h" class="sub" :class="{ bad: !!e.errors.value.same_kind_min }">{{ e.errors.value.same_kind_min ?? '0 значит без паузы.' }}</p>
              </div>
            </div>
            <p class="sub">Повторы одного вида объединяются в следующее уведомление («и ещё N повторов»).</p>
          </fieldset>

          <fieldset class="grp" :disabled="off">
            <legend>Показывать и в тихие часы</legend>
            <label v-for="c in allCats" :key="c.id" class="chk">
              <input type="checkbox" :checked="d.quiet.bypass.includes(c.id)" @change="setBypass(c.id, ($event.target as HTMLInputElement).checked)">
              <span>{{ categoryName(c.id, c.label) }}</span>
            </label>
          </fieldset>

          <div class="grp">
            <h4 class="cap">Статистика</h4>
            <p class="sub">{{ stats }}</p>
          </div>
        </div>
      </details>

      <Transition name="bar">
        <div v-if="e.dirty.value" class="stick">
          <UiChangeBar ref="bar" :line="changeLine || 'Исправьте поля с ошибкой'" :effect="e.hasErrors.value ? 'Пока в полях есть ошибка, применить нельзя.' : e.effect.value" apply="Применить" reset-label="Отменить" :busy="e.busy.value || e.hasErrors.value || !e.canEdit.value" @apply="apply" @reset="cancel" />
        </div>
      </Transition>
      <p class="status" role="status" aria-live="polite">{{ reflectText(e.wait.state.value) }}</p>
    </template>
    <div v-else class="skel" aria-busy="true" />
  </section>
</template>

<style scoped>
.board { display: grid; gap: var(--space-4); max-width: 760px; }
.top { display: grid; gap: 2px; }
.title { margin: 0 0 var(--space-2); font-size: 16px; font-weight: 700; letter-spacing: -0.01em; }
.line { margin: 0; font-size: 15px; font-weight: 600; line-height: 1.4; }
.block { display: grid; gap: var(--space-2); margin-top: var(--space-2); }
.h { margin: 0; font-size: 15px; font-weight: 650; }
.cap { margin: var(--space-2) 0 0; font-size: 12px; font-weight: 600; color: var(--muted); }
.sub { margin: 0; font-size: 12px; line-height: 1.5; color: var(--muted); }
.sub.small { font-size: 12px; }
.sub.bad { color: var(--neg); }
.note { margin: 0; padding: var(--space-3); border-radius: var(--radius-sm); background: var(--panel2); font-size: 12px; line-height: 1.5; color: var(--muted); }
.note.warn { color: var(--warn); }
.rows { list-style: none; margin: 0; padding: 0; background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); }
.row { display: flex; align-items: center; justify-content: space-between; gap: var(--space-4); min-height: 56px; padding: var(--space-3) var(--space-4); }
.rows > .row + .row, .rows > li + li { border-top: 1px solid var(--line); }
.txt { min-width: 0; display: grid; gap: 2px; }
.txt b, .row > b { font-size: 14px; font-weight: 600; overflow-wrap: anywhere; }
.btn { flex: none; min-height: 40px; padding: 0 var(--space-4); font: inherit; font-size: 13px; color: var(--text); background: transparent; border: 1px solid var(--line2); border-radius: var(--radius-sm); cursor: pointer; white-space: nowrap; transition: color var(--dur-fast) ease, border-color var(--dur-fast) ease, opacity var(--dur-fast) ease, transform 120ms var(--ease-out); }
.btn:disabled { opacity: 0.45; cursor: not-allowed; }
.btn:active:not(:disabled) { transform: scale(0.97); }
.btn:focus-visible, .inp:focus-visible, .adv summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.times { display: flex; flex-wrap: wrap; align-items: flex-end; gap: var(--space-3) var(--space-4); }
.fld { display: grid; gap: 4px; align-content: start; min-width: 0; }
.fld.wide { max-width: 320px; }
.fld label { font-size: 12px; color: var(--text); }
.grow { flex: 1 1 220px; align-self: center; }
.inp { min-height: 40px; padding: 0 var(--space-3); font: inherit; font-size: 14px; color: var(--text); background: var(--panel2); border: 1px solid var(--line2); border-radius: var(--radius-sm); color-scheme: light dark; font-variant-numeric: tabular-nums; transition: border-color var(--dur-fast) ease; }
.inp[aria-invalid='true'] { border-color: var(--neg); }
.inp:disabled { opacity: 0.45; cursor: not-allowed; }
.adv { border-top: 1px solid var(--line); padding-top: var(--space-2); }
.adv summary { cursor: pointer; min-height: 40px; display: flex; align-items: center; font-size: 14px; font-weight: 600; }
.adv-in { display: grid; gap: var(--space-5, 20px); padding: var(--space-2) 0 var(--space-3); }
.grp { display: grid; gap: var(--space-2); margin: 0; padding: 0; border: 0; min-width: 0; }
.grp legend { padding: 0; margin-bottom: var(--space-2); font-size: 13px; font-weight: 600; }
.pair { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--space-3) var(--space-4); }
.chk { display: flex; align-items: center; gap: var(--space-3); min-height: 36px; font-size: 13px; cursor: pointer; }
.chk input { width: 18px; height: 18px; accent-color: var(--accent); }
.stick { position: sticky; bottom: 0; z-index: 2; margin: 0 calc(-1 * var(--space-3)); padding: 0 var(--space-3) var(--space-3); background: var(--bg); box-shadow: 0 -12px 12px -8px var(--bg); }
.status { margin: 0; font-size: 12px; color: var(--muted); }
.status:empty { display: none; }
.skel { height: 240px; border-radius: var(--radius); background: var(--panel); }
@media (max-width: 520px) {
  .row { flex-wrap: wrap; }
  .pair { grid-template-columns: minmax(0, 1fr); }
}
@media (hover: hover) and (pointer: fine) {
  .btn:hover:not(:disabled) { border-color: var(--muted); }
}
.bar-enter-from, .reveal-enter-from { opacity: 0; transform: translateY(6px); }
.bar-enter-active, .reveal-enter-active { transition: opacity var(--dur-enter) var(--ease-out), transform var(--dur-enter) var(--ease-out); }
.bar-leave-to, .reveal-leave-to { opacity: 0; }
.bar-leave-active, .reveal-leave-active { transition: opacity 120ms var(--ease-out); }
@media (prefers-reduced-motion: reduce) {
  .bar-enter-from, .reveal-enter-from { transform: none; }
  .btn { transition: color var(--dur-fast) ease, border-color var(--dur-fast) ease, opacity var(--dur-fast) ease; }
  .btn:active:not(:disabled) { transform: none; }
}
</style>
