<script setup lang="ts">
import { computed } from 'vue'
import { groups, issuesOf, pct, placeOf, roleLabel, summary, useRoleStore, ROLE_INFO } from '@/entities/role'
import { UiChip } from '@/shared/ui'
import { fixed } from '@/shared/lib'

/**
 * Блок «Кто думает»: какая модель за какую роль отвечает и как справляется. Сверху вывод одной строкой и замечания (выключенный провайдер запасной модели, много неверных форматов),
 * ниже роли двумя группами: те, что решают о сделках, и общие. В строке роли: название и что она делает, основная модель и где работает, запасные, качество за сутки.
 * Подробности (параметры, бюджет, идентификаторы) в боковой панели по клику. Только чтение.
 */
defineProps<{ selected: string | null }>()
const emit = defineEmits<{ select: [role: string] }>()
const store = useRoleStore()
const s = computed(() => store.snapshot)
const sum = computed(() => (s.value ? summary(s.value) : null))
const grouped = computed(() => (s.value ? groups(s.value) : []))
const issues = computed(() => (s.value?.roles ?? []).flatMap((r) => issuesOf(r, s.value?.mode ?? null).map((i) => ({ ...i, role: r.role }))))
const cost = computed(() => s.value?.usage_today)
</script>

<template>
  <section class="board" aria-label="Кто думает">
    <header class="top">
      <h2 class="title">Кто думает</h2>
      <span v-if="s?.rev != null" class="rev" title="Ревизия назначений ролей">ревизия {{ s.rev }}</span>
    </header>

    <template v-if="s">
      <div class="sum">
        <p class="line">{{ sum?.line }}</p>
        <p class="detail">{{ sum?.detail }}<template v-if="cost && cost.calls"> За сутки {{ cost.calls }} вызовов, {{ fixed((cost.usd ?? 0), 2) }} USD.</template></p>
      </div>

      <ul v-if="issues.length" class="issues" aria-label="Замечания">
        <li v-for="(i, k) in issues" :key="k" :class="i.tone"><b>{{ roleLabel(i.role) }}:</b> {{ i.text }}</li>
      </ul>
      <p v-for="(e, k) in s.errors" :key="'e' + k" class="err">{{ e }}</p>

      <section v-for="g in grouped" :key="g.id" class="group">
        <h3 class="cap">{{ g.title }}</h3>
        <ul class="roles">
          <li v-for="r in g.roles" :key="r.role">
            <button type="button" class="row" :class="{ sel: selected === r.role }" :aria-pressed="selected === r.role" @click="emit('select', r.role)">
              <span class="who">
                <b>{{ roleLabel(r.role) }}</b>
                <UiChip v-if="r.live_risk" title="Роль влияет на решения в live">влияет на live</UiChip>
                <span class="what">{{ ROLE_INFO[r.role]?.what ?? '' }}</span>
              </span>
              <span class="model">
                <b class="mn">{{ r.primary?.model_name ?? 'модель не назначена' }}</b>
                <span class="place">{{ placeOf(r.primary) }}<template v-if="r.primary && !r.primary.provider_enabled"> · провайдер выключен</template></span>
                <span v-if="r.fallback.length" class="fb" :class="{ off: r.fallback.some((f) => !f.provider_enabled) }">запасная: {{ r.fallback.map((f) => f.model_name).join(', ') }}</span>
                <span v-else class="fb none">запасной нет</span>
              </span>
              <span class="qual">
                <span class="calls">{{ r.day.calls ?? 0 }} вызовов за сутки</span>
                <span class="rates">
                  <span :class="{ bad: (r.day.err_rate ?? 0) >= 0.15 && (r.day.calls ?? 0) >= 20 }" title="Доля вызовов, закончившихся ошибкой">ошибки {{ pct(r.day.err_rate) }}</span>
                  <span :class="{ bad: (r.day.fmt_err_rate ?? 0) >= 0.15 && (r.day.calls ?? 0) >= 20 }" title="Доля ответов, не принятых по формату">формат {{ pct(r.day.fmt_err_rate) }}</span>
                </span>
              </span>
              <svg class="chev" viewBox="0 0 12 12" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4.5 3L7.5 6L4.5 9" /></svg>
            </button>
          </li>
        </ul>
      </section>
      <p v-if="!grouped.length" class="empty">Роли не настроены. Они описаны в config/agents.json.</p>
    </template>
    <div v-else class="skel" aria-busy="true" />
  </section>
</template>

<style scoped>
.board { display: grid; gap: var(--space-4); }
.top { display: flex; align-items: baseline; gap: var(--space-3); }
.title { margin: 0; font-size: 16px; font-weight: 700; letter-spacing: -0.01em; margin-right: auto; }
.rev { font-size: 11px; color: var(--muted); }
.sum { padding: var(--space-4) var(--space-5); background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); display: grid; gap: 2px; }
.line { margin: 0; font-size: 16px; font-weight: 600; }
.detail { margin: 0; font-size: 12px; color: var(--muted); }
.issues { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; font-size: 12px; }
.issues li { display: flex; gap: var(--space-2); padding: var(--space-2) var(--space-3); border-radius: var(--radius-sm); background: var(--panel); color: var(--muted); }
.issues li::before { content: ''; flex: none; width: 8px; height: 8px; margin-top: 5px; border-radius: 50%; background: var(--warn); }
.issues li.bad::before { background: var(--neg); }
.issues b { color: var(--text); }
.err { margin: 0; font-size: 12px; color: var(--neg); }
.group { display: grid; gap: var(--space-2); }
.cap { margin: 0; font-size: 12px; font-weight: 600; color: var(--muted); }
.roles { list-style: none; margin: 0; padding: 0; background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); overflow: hidden; }
.roles li + li { border-top: 1px solid var(--line); }
.row { display: grid; grid-template-columns: minmax(0, 1.5fr) minmax(0, 1.1fr) minmax(0, 0.8fr) 12px; gap: var(--space-4); align-items: start; width: 100%; padding: var(--space-3) var(--space-4); background: none; border: 0; color: inherit; font: inherit; text-align: left; cursor: pointer; transition: background-color var(--dur-fast) ease; }
.row.sel { background: var(--panel2); box-shadow: inset 3px 0 0 var(--accent); }
.row:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.who { display: flex; flex-wrap: wrap; align-items: center; gap: 4px var(--space-2); font-size: 14px; }
.what { flex: 1 1 100%; font-size: 12px; color: var(--muted); line-height: 1.4; }
.model { display: grid; gap: 2px; font-size: 12px; min-width: 0; }
.mn { font-size: 13px; overflow-wrap: anywhere; }
.place { color: var(--muted); }
.fb { color: var(--muted); overflow-wrap: anywhere; }
.fb.off { color: var(--warn); }
.fb.none { opacity: 0.7; }
.qual { display: grid; gap: 2px; font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.rates { display: flex; flex-wrap: wrap; gap: 2px var(--space-3); }
.rates .bad { color: var(--warn); font-weight: 600; }
.chev { margin-top: 4px; color: var(--muted); }
.empty { margin: 0; font-size: 13px; color: var(--muted); }
.skel { height: 240px; border-radius: var(--radius); background: var(--panel); }
@media (max-width: 900px) {
  .row { grid-template-columns: minmax(0, 1fr) 12px; }
  .row .model, .row .qual { grid-column: 1; }
}
@media (hover: hover) and (pointer: fine) {
  .row:not(.sel):hover { background: var(--panel2); }
}
</style>
