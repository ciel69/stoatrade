<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ThoughtBubble, ThoughtCloud, emptyNote, pickThoughts, plural, roleAnswer, roleName, roleWhat, stateWords, todayText, useBrainStore, useQuip } from '@/entities/brain'
import { UiBrainIcon, type BrainIconState } from '@/shared/ui'
import { ageText, plainify, proper, usd } from '@/shared/lib'

const brain = useBrainStore()
const now = ref(Date.now() / 1000)
const paused = ref(false)                       // пока курсор или фокус на облаке, время для мыслей стоит: можно дочитать, ничего не гаснет
const hold = ref(false)                         // курсор или фокус на мозге и облаке: реплика в облаке не меняется
let timer: ReturnType<typeof setInterval> | null = null

/*
 * Отладка без сервера: ?brain=mock | mock-think | mock-down | mock-protect подставляет пример снимка; ?brain сам по себе открывает роли (для ссылок и скриншотов),
 * &view=thoughts оставляет ленту мыслей.
 */
const query = new URLSearchParams(location.search)
const dbg = query.get('brain')
if (dbg?.startsWith('mock')) brain.mock(dbg === 'mock-think' ? 'thinking' : dbg === 'mock-down' ? 'down' : dbg === 'mock-protect' ? 'protect' : 'idle')

onMounted(() => {
  brain.start()
  timer = setInterval(() => {
    if (!paused.value) now.value = Date.now() / 1000
  }, 1000)
  window.addEventListener('keydown', onKey)
})
onUnmounted(() => {
  if (timer) clearInterval(timer)
  window.removeEventListener('keydown', onKey)
})

/*
 * Клик, тап или Enter по мозгу открывает роли поверх ленты мыслей, того же размера, панель не растёт: имя роли, чем занимается, какая модель отвечала и сколько стоила
 * сегодня. Отдельной текстовой кнопки нет: подсказка это title, подпись для скринридера и маленький шеврон у мозга. Мозг при клике ещё и пускает каскад импульсов.
 * Только чтение: никаких команд платформе. Esc и повторное нажатие закрывают.
 */
const open = ref(query.has('brain') && query.get('view') !== 'thoughts')
const trigger = ref<HTMLButtonElement | null>(null)
const icon = ref<InstanceType<typeof UiBrainIcon> | null>(null)

function toggle(ev: MouseEvent): void {
  open.value = !open.value
  icon.value?.fire(ev)
}
function onKey(ev: KeyboardEvent): void {
  if (ev.key === 'Escape' && open.value) {
    open.value = false
    trigger.value?.focus()
  }
}

const snap = computed(() => brain.snapshot)
const loaded = computed(() => snap.value !== null)
const iconState = computed<BrainIconState>(() => (loaded.value ? brain.status : 'idle'))        // до первого снимка не показываем «не работает»
const words = computed(() => stateWords(loaded.value, brain.status))
const thoughts = computed(() => pickThoughts(brain.decisions, now.value))
const active = computed(() => snap.value?.active ?? null)
const spend = computed(() => brain.roles.reduce((s, r) => s + (r.today?.usd ?? 0), 0))
const calls = computed(() => brain.roles.reduce((s, r) => s + (r.today?.calls ?? 0), 0))
const waiting = computed(() => snap.value?.queue?.new ?? 0)
const provider = (p: string | null | undefined): string => (!p ? '' : p === 'local' ? 'локальная' : proper(p))
const rows = computed(() => brain.roles.map((r) => ({
  role: r.role, name: roleName(r.role), what: roleWhat(r.role), model: r.last?.profile ?? r.primary?.id ?? null, last: r.last,
  calls: r.today?.calls ?? 0, usd: r.today?.usd ?? 0, errors: r.today?.errors ?? 0, fb: r.last?.fallback_from ?? null,
})))
const fell = computed(() => brain.roles.filter((r) => r.last?.fallback_from).length)       // отвечал запасной профиль (сервер помечает fallback_from)
// причина режима защиты пишется сервером для владельца; если в ней служебные следы, показываем общую фразу
const protectNote = computed(() => (snap.value?.protect_reason ? plainify(snap.value.protect_reason) ?? 'Сработало ограничение, из-за которого мозг не открывает новые сделки.' : null))

// облако настроения: только по данным состояния из WebSocket, словарь шуток на клиенте (entities/brain/lib/mood.ts)
const status = computed(() => brain.status)
const quip = useQuip(status, loaded, hold)
const tip = computed(() => (open.value ? 'Вернуться к мыслям' : 'Показать, кто за что отвечает'))
</script>

<template>
  <section class="panel" aria-labelledby="brain-title">
    <h2 id="brain-title" class="title">Что делает мозг</h2>

    <header class="top" @pointerenter="hold = true" @pointerleave="hold = false" @focusin="hold = true" @focusout="hold = false">
      <button ref="trigger" type="button" class="brain-btn" :aria-expanded="open" aria-controls="brain-roles"
              :aria-label="`Мозг: ${words.title.toLowerCase()}. ${tip}`" :title="tip" @click="toggle">
        <span class="ico"><UiBrainIcon ref="icon" :state="iconState" :size="128" interactive label="" /></span>
        <span class="chev" aria-hidden="true">
          <svg viewBox="0 0 12 12" width="10" height="10" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M3 4.5 6 7.5 9 4.5" /></svg>
        </span>
      </button>
      <div class="slot"><ThoughtCloud :text="quip" /></div>
      <div class="who" aria-live="polite">
        <div class="state" :class="loaded ? `st-${brain.status}` : ''">{{ words.title }}</div>
        <p class="hint">{{ words.hint }}</p>
      </div>
    </header>

    <p v-if="protectNote" class="alert">{{ protectNote }}</p>
    <p v-else-if="fell" class="alert soft">Основная модель не ответила: у {{ fell }} {{ plural(fell, 'роли', 'ролей', 'ролей') }} сейчас отвечает запасная.</p>

    <p v-if="loaded" class="facts">
      <template v-if="active">
        <span><span class="k">Модель:</span> {{ active.profile ?? 'без названия' }}<template v-if="provider(active.provider)">, {{ provider(active.provider) }}</template></span>
        <span><span class="k">Ответила:</span> {{ ageText(active.t, now) }}<template v-if="active.role">, задача «{{ roleName(active.role) }}»</template></span>
      </template>
      <span v-else><span class="k">Модель:</span> ещё не отвечала</span>
    </p>

    <h3 class="cap">{{ open ? 'Кто за что отвечает' : 'Последние мысли' }}</h3>

    <!-- область под мысли фиксированной высоты: новые не раздвигают панель, лишние прокручиваются -->
    <div class="stage">
      <div class="sky" @pointerenter="paused = true" @pointerleave="paused = false" @focusin="paused = true" @focusout="paused = false">
        <TransitionGroup name="feed" tag="ul" class="feedlist" aria-label="Последние мысли мозга" aria-live="polite">
          <ThoughtBubble v-for="t in thoughts" :key="t.row.seq" :thought="t" :now="now" />
        </TransitionGroup>
        <p v-if="!thoughts.length" class="silence">{{ emptyNote(loaded, brain.status) }}</p>
      </div>

      <Transition name="roles">
        <div v-if="open" id="brain-roles" class="roles" role="region" aria-label="Кто за что отвечает: роли мозга и их модели" tabindex="0">
          <ul v-if="rows.length" class="rlist">
            <li v-for="r in rows" :key="r.role" class="role">
              <div class="rtop">
                <b class="rname">{{ r.name }}</b>
                <span class="rage"><span class="dot" :class="r.last == null ? 'none' : r.last.ok === false ? 'bad' : 'ok'" aria-hidden="true" />{{ roleAnswer(r.last, now) }}</span>
              </div>
              <p class="rwhat">{{ r.what }}</p>
              <p class="rsub">
                Модель: {{ r.model ?? 'не назначена' }} · {{ todayText(r.calls, usd(r.usd)) }}<template v-if="r.errors"> · ошибок: {{ r.errors }}</template>
                <template v-if="r.fb"> · отвечает запасная модель вместо {{ r.fb }}</template>
              </p>
            </li>
          </ul>
          <p v-else class="rnone">Данных по ролям пока нет.</p>
        </div>
      </Transition>
    </div>

    <footer class="foot">
      <span title="События, которые мозг ещё не успел разобрать."><span class="k">Ждут разбора:</span> {{ waiting }}</span>
      <span title="Сколько раз сегодня платформа обращалась к моделям мозга."><span class="k">Запросов сегодня:</span> {{ calls }}</span>
      <span title="Сколько сегодня стоили запросы к моделям мозга."><span class="k">Расход сегодня:</span> {{ usd(spend) }}</span>
    </footer>
  </section>
</template>

<style scoped>
.panel { container-type: inline-size; background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: var(--space-4); display: flex; flex-direction: column; gap: var(--space-3); min-width: 0; }
.title { margin: 0; font-size: 14px; font-weight: 600; }

/* мозг главный: слева крупно на всю высоту, справа облако мысли над состоянием; облако занимает свою строку всегда, поэтому панель не прыгает */
.top { --brain-size: 128px; display: grid; grid-template-columns: var(--brain-size) minmax(0, 1fr); grid-template-rows: 46px auto; column-gap: var(--space-4); align-items: start; }
.slot { grid-column: 2; grid-row: 1; align-self: end; min-width: 0; padding-bottom: 2px; }
.who { grid-column: 2; grid-row: 2; min-width: 0; padding-top: var(--space-2); }
.state { font-size: 20px; font-weight: 700; letter-spacing: -0.02em; line-height: 1.15; }
.hint { margin: var(--space-1) 0 0; font-size: 13px; line-height: 1.4; color: var(--muted); }
.st-thinking { color: var(--zone-brain); }
.st-protect { color: var(--warn); }
.st-down { color: var(--neg); }
.st-idle { color: var(--pos); }
.alert { margin: 0; padding: var(--space-2) var(--space-3); border: 1px solid var(--warn); border-radius: var(--radius-sm); color: var(--warn); font-size: 13px; }
.alert.soft { border-color: var(--line2); color: var(--muted); }
.facts { margin: 0; display: grid; gap: 2px; font-size: 12px; color: var(--text); overflow-wrap: anywhere; }
.k { color: var(--muted); }

/* мозг стоит прямо на панели: ни фона, ни рамки (и при наведении, и при открытых ролях); фокус с клавиатуры это круглое кольцо по форме */
.brain-btn { grid-column: 1; grid-row: 1 / 3; position: relative; width: var(--brain-size); height: var(--brain-size); padding: 0; background: none; border: 0; border-radius: 50%; color: inherit; cursor: pointer; display: block; -webkit-tap-highlight-color: transparent; }
.ico { display: block; width: 100%; height: 100%; transition: transform 160ms var(--ease-out); }
.ico :deep(svg) { display: block; width: 100%; height: 100%; }
.brain-btn:active .ico { transform: scale(0.97); }
.brain-btn:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
.roles:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
/* едва заметный шеврон: мозг раскрывается, роли под ним; поворачивается, когда роли открыты */
.chev { position: absolute; right: 2px; bottom: 8px; width: 20px; height: 20px; border-radius: 50%; display: grid; place-items: center; background: var(--panel2); border: 1px solid var(--line2); color: var(--muted); opacity: 0.7; transition: opacity var(--dur-fast) ease; pointer-events: none; }
.chev svg { transition: transform var(--dur-enter) var(--ease-out); }
.brain-btn[aria-expanded='true'] .chev svg { transform: rotate(180deg); }
.brain-btn:focus-visible .chev { opacity: 1; }
@media (hover: hover) and (pointer: fine) {
  .brain-btn:hover .chev { opacity: 1; }
}

.cap { margin: 0; font-size: 13px; font-weight: 600; }

.stage { position: relative; }
.sky { --thought-h: 76px; position: relative; height: calc(var(--thought-h) * 3 + var(--space-2) * 2); overflow-y: auto; scrollbar-gutter: stable; }

/* роли поверх ленты мыслей: тот же размер, панель не растёт; редкое открытие, поэтому короткий выезд, закрытие быстрее */
.roles { position: absolute; inset: 0; overflow-y: auto; scrollbar-gutter: stable; background: var(--panel); border: 1px solid var(--line2); border-radius: var(--radius-sm); padding: var(--space-2); }
.rlist { list-style: none; margin: 0; padding: 0; display: grid; gap: var(--space-1); }
.role { padding: var(--space-2) var(--space-3); border-radius: var(--radius-sm); transition: background-color var(--dur-fast) ease; }
@media (hover: hover) and (pointer: fine) {
  .role:hover { background: var(--panel2); }
}
.rtop { display: flex; align-items: baseline; gap: var(--space-2); min-width: 0; }
.rname { font-size: 13px; }
.rage { margin-left: auto; font-size: 12px; color: var(--muted); white-space: nowrap; font-variant-numeric: tabular-nums; }
.rwhat { margin: 2px 0 0; font-size: 12px; line-height: 1.4; color: var(--text); }
.rsub { margin: 2px 0 0; font-size: 12px; line-height: 1.4; color: var(--muted); font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.dot { display: inline-block; width: 6px; height: 6px; margin-right: 6px; border-radius: 50%; vertical-align: middle; background: var(--line2); }
.dot.ok { background: var(--pos); }
.dot.bad { background: var(--neg); }
.rnone { margin: 0; height: 100%; display: grid; place-items: center; color: var(--muted); font-size: 13px; }
.roles-enter-from, .roles-leave-to { opacity: 0; }
.roles-enter-from { transform: translateY(6px); }
.roles-enter-active { transition: opacity var(--dur-enter) var(--ease-out), transform var(--dur-enter) var(--ease-out); }
.roles-leave-active { transition: opacity 120ms var(--ease-out); }
@media (prefers-reduced-motion: reduce) {
  .roles-enter-from { transform: none; }
  .role, .chev, .chev svg { transition: none; }
  .ico { transition: none; }
}
.feedlist { list-style: none; margin: 0; padding: 0; display: grid; gap: var(--space-2); position: relative; }
.silence { position: absolute; inset: 0; margin: 0; padding: 0 var(--space-4); text-align: center; display: grid; place-items: center; color: var(--muted); font-size: 13px; }
.foot { margin-top: auto; padding-top: var(--space-2); border-top: 1px solid var(--line); display: flex; flex-wrap: wrap; gap: var(--space-1) var(--space-4); font-size: 12px; font-variant-numeric: tabular-nums; }

/* широкая колонка (три колонки на 1920): мозг крупнее */
@container (min-width: 460px) {
  .top { --brain-size: 144px; grid-template-rows: 50px auto; }
}
/* узкая панель (одна колонка на телефоне или тесная колонка на ноутбуке): мозг чуть меньше, облако остаётся рядом */
@container (max-width: 360px) {
  .top { --brain-size: 96px; column-gap: var(--space-3); grid-template-rows: 44px auto; }
}
</style>
