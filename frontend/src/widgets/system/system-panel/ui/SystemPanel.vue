<script setup lang="ts">
import { computed } from 'vue'
import { useControlStore } from '@/entities/control'
import { useSystemStore } from '@/entities/system'
import { UiDetail } from '@/shared/ui'
import { actionName, commandStatus, consumerName, contextName, decisionReason, duration, failureText, feedState, gateName, holdText, humanize, planState, proper, sessionName, stopNote, thesisText, warningName } from '@/shared/lib'

/**
 * Боковая панель подробностей системы: что нужно для живой торговли (все условия простым языком), исполнитель и очередь, откуда берутся данные, история сессий и команд.
 * Только чтение. Служебные коды и тексты сервера переводит общий словарь; исходные тексты лежат в «Подробностях для специалиста».
 */
const props = defineProps<{ kind: string }>()
defineEmits<{ close: [] }>()
const control = useControlStore()
const sys = useSystemStore()
const s = computed(() => sys.snapshot)

const TITLE: Record<string, string> = { ready: 'Живая торговля', exec: 'Исполнитель и очередь', sources: 'Откуда берутся данные', history: 'История' }
const mode = computed(() => control.ctl?.platform.mode ?? null)
const live = computed(() => mode.value === 'live')

interface Item { id: string; name: string; ok: boolean; title: string; text: string; tone: string; required: boolean }
const items = computed<Item[]>(() => (control.ctl?.checks ?? []).map((c) => {
  const h = humanize({ kind: 'gate', id: c.id, name: c.name, ok: c.ok, msg: c.msg, sev: c.sev }, { mode: mode.value, running: control.ctl?.platform.state === 'running' })
  const name = gateName(c.id) ?? warningName(c.id) ?? h.title
  return { id: c.id, name, ok: c.ok, title: c.ok ? name : h.title, text: c.ok ? '' : [h.what, h.todo].filter(Boolean).join(' '), tone: c.ok ? 'ok' : h.tone, required: c.sev === 'red' }
}))
const missing = computed(() => items.value.filter((i) => !i.ok && i.required))
const advice = computed(() => items.value.filter((i) => !i.ok && !i.required))
const done = computed(() => items.value.filter((i) => i.ok))
const tech = computed(() => (control.ctl?.checks ?? []).filter((c) => !c.ok).map((c) => `${c.id} · ${c.name} · ${c.msg}`).join('\n'))

const fmt = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false })
const at = (iso: string | null) => { const t = iso ? Date.parse(iso) : NaN; return Number.isNaN(t) ? '–' : fmt.format(new Date(t)) }
const lasted = (a: string | null, b: string | null) => { const x = a ? Date.parse(a) : NaN; const y = b ? Date.parse(b) : NaN; return Number.isNaN(x) || Number.isNaN(y) ? null : duration((y - x) / 1000) }
const plans = computed(() => Object.entries(s.value?.exec.plans ?? {}).map(([k, v]) => `${planState(k)}: ${v}`).join(' · ') || '–')
const hold = computed(() => holdText(s.value?.exec.hold))
const reject = (code: string) => decisionReason(code)
</script>

<template>
  <UiDetail :label="TITLE[props.kind] ?? 'Подробности'" @close="$emit('close')">
    <template #title><b class="h">{{ TITLE[props.kind] ?? 'Подробности' }}</b></template>

    <template v-if="props.kind === 'ready'">
      <p class="lead">
        Платформа всегда проверяет, готова ли она торговать на реальные деньги.
        <template v-if="live">Сейчас такая торговля идёт, поэтому невыполненное условие требует внимания.</template>
        <template v-else>Сейчас она торгует не на реальные деньги, поэтому невыполненные условия ничему не мешают: это подсказка, что нужно сделать, если решите перейти.</template>
      </p>
      <template v-if="items.length">
        <h4 v-if="missing.length" class="sub">Чего не хватает</h4>
        <ul v-if="missing.length" class="list">
          <li v-for="c in missing" :key="c.id" :class="c.tone === 'bad' ? 'bad' : c.tone === 'warn' ? 'warn' : 'info'">
            <i class="dot" aria-hidden="true" />
            <div><b>{{ c.title }}</b><p class="msg">{{ c.text }}</p></div>
          </li>
        </ul>
        <h4 v-if="advice.length" class="sub">Рекомендации</h4>
        <ul v-if="advice.length" class="list">
          <li v-for="c in advice" :key="c.id" :class="c.tone === 'warn' ? 'warn' : 'info'">
            <i class="dot" aria-hidden="true" />
            <div><b>{{ c.title }}</b><p class="msg">{{ c.text }}</p></div>
          </li>
        </ul>
        <h4 v-if="done.length" class="sub">Уже выполнено</h4>
        <ul v-if="done.length" class="list plain">
          <li v-for="c in done" :key="c.id" class="ok"><i class="dot" aria-hidden="true" />{{ c.name }}</li>
        </ul>
        <details v-if="tech" class="tech"><summary>Подробности для специалиста</summary><pre>{{ tech }}</pre></details>
      </template>
      <p v-else class="muted">Условия приходят от службы управления. Сейчас их нет: она не отвечает или платформа ещё не проверяла.</p>
    </template>

    <template v-else-if="props.kind === 'exec' && s">
      <dl class="kv">
        <div><dt>Исполнитель (ставит ордера)</dt><dd>{{ s.exec.present ? 'работает' : 'не запущен' }}</dd></div>
        <div><dt>Скорость реакции</dt><dd>{{ s.exec.lag_ms <= 50 ? 'отличная' : s.exec.lag_ms <= 250 ? 'нормальная' : 'замедленная' }} ({{ s.exec.lag_ms }} мс)</dd></div>
        <div><dt>Позиций на бирже</dt><dd>{{ s.exec.positions ?? 0 }}</dd></div>
        <div><dt>Планы в исполнителе</dt><dd>{{ plans }}</dd></div>
        <div v-if="hold"><dt>Исполнитель на паузе</dt><dd>{{ hold }}</dd></div>
      </dl>
      <h4 class="sub">Очередь событий</h4>
      <p class="muted">Так платформа передаёт события мозгу и дашборду. «Ждут» — события, которые ещё не обработаны.</p>
      <ul v-if="Object.keys(s.exec.consumers).length" class="list plain">
        <li v-for="(c, name) in s.exec.consumers" :key="name"><b>{{ consumerName(String(name)) }}</b>: обработано {{ c.acked ?? 0 }}, ждут {{ c.unacked ?? 0 }}</li>
      </ul>
      <p v-else class="muted">Получателей пока нет.</p>
      <h4 class="sub">Отказы защитных правил за сутки</h4>
      <p v-if="s.rejects.codes.length" class="muted">Из {{ s.rejects.n }} решений отклонено {{ s.rejects.rejects }}. Причины: {{ s.rejects.codes.map((c) => `${reject(c.code)} (${c.n})`).join('; ') }}.</p>
      <p v-else class="muted">Отказов не было.</p>
    </template>

    <template v-else-if="props.kind === 'sources' && s">
      <h4 class="sub">Цены с бирж</h4>
      <ul v-if="s.sources.feed.length" class="list">
        <li v-for="f in s.sources.feed" :key="f.id" :class="f.state === 'ok' ? 'ok' : 'warn'">
          <i class="dot" aria-hidden="true" />
          <div><b>{{ proper(f.id) }}</b><p class="msg">{{ feedState(f.state) }}<template v-if="f.reconnects"> · переподключений: {{ f.reconnects }}</template></p></div>
        </li>
      </ul>
      <p v-else class="muted">Поток цен пока не сообщает своё состояние.</p>
      <h4 class="sub">Справочные данные о рынке</h4>
      <ul v-if="Object.keys(s.sources.context).length" class="chips">
        <li v-for="(ok, name) in s.sources.context" :key="name" :class="ok ? 'ok' : 'warn'"><span class="mark" aria-hidden="true">{{ ok ? '✓' : '!' }}</span> {{ contextName(String(name)) }}{{ ok ? '' : ': не отвечает' }}</li>
      </ul>
      <p v-else class="muted">Данных пока нет.</p>
      <p v-if="s.sources.degraded.length" class="note">Не отвечают: {{ s.sources.degraded.map((d) => contextName(d)).join(', ') }}. Мозг получает меньше сведений о рынке; торговля идёт.</p>
    </template>

    <template v-else-if="props.kind === 'history' && s">
      <h4 class="sub">Последние команды</h4>
      <ul v-if="control.ctl?.last?.length" class="list">
        <li v-for="(r, i) in control.ctl.last" :key="i" :class="r.code === 'OK' ? 'ok' : 'bad'">
          <i class="dot" aria-hidden="true" />
          <div><b>{{ actionName(r.action) }}</b><p class="msg">{{ r.code === 'OK' ? 'Выполнено' : failureText(r.code, r.msg) }}<template v-if="commandStatus(r.status) && r.code !== 'OK'"> ({{ commandStatus(r.status) }})</template></p></div>
        </li>
      </ul>
      <p v-else class="muted">Команд ещё не было.</p>
      <h4 class="sub">Торговые сессии</h4>
      <ul v-if="s.sessions.length" class="list">
        <li v-for="x in s.sessions" :key="x.id" class="sess">
          <div>
            <b>{{ sessionName(x.type) }}</b>
            <p class="msg">{{ at(x.ts) }}{{ x.stopped ? ` → ${at(x.stopped)}` : ' · идёт' }}<template v-if="lasted(x.ts, x.stopped)"> ({{ lasted(x.ts, x.stopped) }})</template><template v-if="stopNote(x.stop_note)"> · {{ stopNote(x.stop_note) }}</template></p>
            <p v-if="thesisText(x.thesis)" class="msg">{{ thesisText(x.thesis) }}</p>
          </div>
        </li>
      </ul>
      <p v-else class="muted">Сессий ещё не было.</p>
    </template>
  </UiDetail>
</template>

<style scoped>
.h { font-size: 16px; }
.lead, .muted, .msg { margin: 0; font-size: 12px; color: var(--muted); line-height: 1.5; text-wrap: pretty; }
.msg { margin-top: 2px; overflow-wrap: anywhere; }
.sub { margin: var(--space-2) 0 0; font-size: 12px; font-weight: 600; color: var(--muted); }
.list { list-style: none; margin: 0; padding: 0; display: grid; gap: var(--space-3); }
.list li { display: grid; grid-template-columns: 8px minmax(0, 1fr); gap: var(--space-3); align-items: start; font-size: 13px; }
.list li.sess { grid-template-columns: minmax(0, 1fr); }
.list.plain li { display: block; font-size: 12px; }
.list.plain li.ok { display: flex; align-items: center; gap: var(--space-2); color: var(--muted); }
.dot { margin-top: 6px; width: 8px; height: 8px; border-radius: 50%; background: var(--line2); flex: none; }
.list.plain .dot { margin-top: 0; }
.ok .dot { background: var(--pos); }
.warn .dot { background: var(--warn); }
.bad .dot { background: var(--neg); }
.info .dot { background: var(--muted); }
.kv { margin: 0; display: grid; gap: 6px; font-size: 12px; }
.kv > div { display: flex; justify-content: space-between; gap: var(--space-4); }
dt { color: var(--muted); }
dd { margin: 0; text-align: right; font-variant-numeric: tabular-nums; }
.chips { list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: var(--space-2); font-size: 12px; }
.chips li { padding: 2px 10px; border: 1px solid var(--line2); border-radius: 99px; }
.chips li.warn { color: var(--warn); border-color: var(--warn); }
.note { margin: 0; padding: var(--space-3); border-radius: var(--radius-sm); background: var(--panel2); font-size: 12px; line-height: 1.5; color: var(--warn); }
.tech { font-size: 12px; color: var(--muted); }
.tech summary { cursor: pointer; min-height: 32px; display: flex; align-items: center; }
.tech summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 2px; }
.tech pre { margin: var(--space-1) 0 0; padding: var(--space-3); background: var(--panel2); border-radius: var(--radius-sm); font-family: var(--mono, ui-monospace, monospace); font-size: 11px; white-space: pre-wrap; overflow-wrap: anywhere; user-select: all; }
</style>
