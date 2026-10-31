<script setup lang="ts">
import { computed } from 'vue'
import { useControlStore } from '@/entities/control'
import { useSystemStore } from '@/entities/system'
import { UiDetail } from '@/shared/ui'
import { duration } from '@/shared/lib'

/**
 * Боковая панель подробностей системы: готовность к live (все условия допуска со статусом и причиной), исполнитель и очередь, источники данных, история сессий и команд.
 * Только чтение: условия «красные» обойти нельзя, исправлять причину (см. docs/CONTROL.md), «янтарные» владелец принимает при старте дня.
 */
const props = defineProps<{ kind: string }>()
defineEmits<{ close: [] }>()
const control = useControlStore()
const sys = useSystemStore()
const s = computed(() => sys.snapshot)

const TITLE: Record<string, string> = { ready: 'Готовность к live', exec: 'Исполнитель и очередь', sources: 'Источники данных', history: 'История' }
const checks = computed(() => [...(control.ctl?.checks ?? [])].sort((a, b) => Number(a.ok) - Number(b.ok) || (a.sev === 'red' ? 0 : 1) - (b.sev === 'red' ? 0 : 1)))
const fmt = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false })
const at = (iso: string | null) => { const t = iso ? Date.parse(iso) : NaN; return Number.isNaN(t) ? '–' : fmt.format(new Date(t)) }
const lasted = (a: string | null, b: string | null) => { const x = a ? Date.parse(a) : NaN; const y = b ? Date.parse(b) : NaN; return Number.isNaN(x) || Number.isNaN(y) ? null : duration((y - x) / 1000) }
const CODE: Record<string, string> = { OK: 'выполнено' }
</script>

<template>
  <UiDetail :label="TITLE[props.kind] ?? 'Подробности'" @close="$emit('close')">
    <template #title><b class="h">{{ TITLE[props.kind] ?? 'Подробности' }}</b></template>

    <template v-if="props.kind === 'ready'">
      <p class="lead">Условия допуска к реальным деньгам. Жёсткие (красные) обойти нельзя, нужно исправить причину; предупреждения (янтарные) владелец принимает вручную при старте дня.</p>
      <ul v-if="checks.length" class="list">
        <li v-for="c in checks" :key="c.id" :class="c.ok ? 'ok' : c.sev === 'red' ? 'bad' : 'warn'">
          <i class="dot" aria-hidden="true" />
          <div><b>{{ c.name }}</b><span class="tag">{{ c.sev === 'red' ? 'жёсткое' : 'предупреждение' }}</span><p v-if="c.msg" class="msg">{{ c.msg }}</p></div>
        </li>
      </ul>
      <p v-else class="muted">Условия приходят от сервиса управления; сейчас их нет (ctl не отвечает или платформа ещё не проверяла).</p>
    </template>

    <template v-else-if="props.kind === 'exec' && s">
      <dl class="kv">
        <div><dt>Исполнитель</dt><dd>{{ s.exec.present ? 'запущен' : 'не запущен' }}</dd></div>
        <div><dt>Задержка цикла</dt><dd>{{ s.exec.lag_ms }} мс</dd></div>
        <div><dt>Позиций на бирже</dt><dd>{{ s.exec.positions ?? 0 }}</dd></div>
        <div><dt>Планы в исполнителе</dt><dd>{{ Object.entries(s.exec.plans).map(([k, v]) => `${k} ${v}`).join(' · ') || '–' }}</dd></div>
        <div v-if="s.exec.hold"><dt>Пауза (hold)</dt><dd>{{ s.exec.hold }}</dd></div>
      </dl>
      <h4 class="sub">Очередь событий</h4>
      <ul v-if="Object.keys(s.exec.consumers).length" class="list plain">
        <li v-for="(c, name) in s.exec.consumers" :key="name"><b>{{ name }}</b> подтверждено {{ c.acked ?? 0 }}, не подтверждено {{ c.unacked ?? 0 }}</li>
      </ul>
      <p v-else class="muted">Потребителей нет.</p>
      <h4 class="sub">Отказы Gate за сутки</h4>
      <p v-if="s.rejects.codes.length" class="muted">{{ s.rejects.rejects }} из {{ s.rejects.n }} решений: {{ s.rejects.codes.map((c) => `${c.code} ×${c.n}`).join(', ') }}</p>
      <p v-else class="muted">Отказов нет.</p>
    </template>

    <template v-else-if="props.kind === 'sources' && s">
      <h4 class="sub">Фид цен</h4>
      <ul v-if="s.sources.feed.length" class="list">
        <li v-for="f in s.sources.feed" :key="f.id" :class="f.state === 'ok' ? 'ok' : 'warn'">
          <i class="dot" aria-hidden="true" />
          <div><b>{{ f.id }}</b><p class="msg">{{ f.state ?? '?' }} · переподключений {{ f.reconnects ?? '–' }} · ошибок {{ f.errors ?? 0 }}<template v-if="f.error"> · {{ f.error }}</template></p></div>
        </li>
      </ul>
      <p v-else class="muted">Фид не отдаёт состояние (runtime/health.json).</p>
      <h4 class="sub">Контекст рынка</h4>
      <ul v-if="Object.keys(s.sources.context).length" class="chips">
        <li v-for="(ok, name) in s.sources.context" :key="name" :class="ok ? 'ok' : 'warn'">{{ ok ? '✓' : '!' }} {{ name }}</li>
      </ul>
      <p v-else class="muted">Нет данных (runtime/context.json).</p>
      <p v-if="s.sources.degraded.length" class="note">Деградировали: {{ s.sources.degraded.join(', ') }}</p>
    </template>

    <template v-else-if="props.kind === 'history' && s">
      <h4 class="sub">Последние команды</h4>
      <ul v-if="control.ctl?.last?.length" class="list">
        <li v-for="(r, i) in control.ctl.last" :key="i" :class="r.code === 'OK' ? 'ok' : 'bad'">
          <i class="dot" aria-hidden="true" />
          <div><b>{{ r.action || '?' }}</b><p class="msg">{{ r.code === 'OK' ? CODE.OK : 'отказано: ' + (r.msg || r.code) }}<template v-if="r.status"> · {{ r.status }}</template></p></div>
        </li>
      </ul>
      <p v-else class="muted">Команд ещё не было.</p>
      <h4 class="sub">Сессии</h4>
      <ul v-if="s.sessions.length" class="list">
        <li v-for="x in s.sessions" :key="x.id" class="sess">
          <div>
            <b>{{ x.type ?? '?' }}</b> <span class="muted mono">{{ x.id }}</span>
            <p class="msg">{{ at(x.ts) }}{{ x.stopped ? ` → ${at(x.stopped)}` : ' · идёт' }}<template v-if="lasted(x.ts, x.stopped)"> ({{ lasted(x.ts, x.stopped) }})</template><template v-if="x.stop_note"> · {{ x.stop_note }}</template></p>
            <p v-if="x.thesis" class="msg">{{ x.thesis }}</p>
          </div>
        </li>
      </ul>
      <p v-else class="muted">Сессий ещё не было.</p>
    </template>
  </UiDetail>
</template>

<style scoped>
.h { font-size: 16px; }
.lead, .muted, .msg { margin: 0; font-size: 12px; color: var(--muted); line-height: 1.5; }
.msg { margin-top: 2px; overflow-wrap: anywhere; }
.sub { margin: var(--space-2) 0 0; font-size: 12px; font-weight: 600; color: var(--muted); }
.list { list-style: none; margin: 0; padding: 0; display: grid; gap: var(--space-3); }
.list li { display: grid; grid-template-columns: 8px minmax(0, 1fr); gap: var(--space-3); align-items: start; font-size: 13px; }
.list li.sess { grid-template-columns: minmax(0, 1fr); }
.list.plain li { display: block; font-size: 12px; }
.dot { margin-top: 6px; width: 8px; height: 8px; border-radius: 50%; background: var(--line2); }
.ok .dot { background: var(--pos); }
.warn .dot { background: var(--warn); }
.bad .dot { background: var(--neg); }
.tag { margin-left: var(--space-2); font-size: 11px; color: var(--muted); }
.kv { margin: 0; display: grid; gap: 6px; font-size: 12px; }
.kv > div { display: flex; justify-content: space-between; gap: var(--space-4); }
dt { color: var(--muted); }
dd { margin: 0; text-align: right; font-variant-numeric: tabular-nums; }
.chips { list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: var(--space-2); font-size: 12px; }
.chips li { padding: 2px 10px; border: 1px solid var(--line2); border-radius: 99px; }
.chips li.warn { color: var(--warn); border-color: var(--warn); }
.note { margin: 0; padding: var(--space-3); border-radius: var(--radius-sm); background: var(--panel2); font-size: 12px; color: var(--warn); }
.mono { font-family: var(--mono, ui-monospace, monospace); font-size: 11px; }
</style>
