<script setup lang="ts">
import { computed, ref } from 'vue'
import { groups, issuesOf, placeOf, remarkText, roleLabel, summary, useRoleStore } from '@/entities/role'
import { UiChip } from '@/shared/ui'

/**
 * Блок «Кто думает»: какая модель за какую роль отвечает. Сверху ответ одной фразой («все роли думают одной моделью»), замечания только важные (два, остальные по кнопке),
 * ниже роли двумя группами: те, что решают о сделках, и помощники. В строке только название роли, модель и где она работает, запасная модель, пометка «есть замечания».
 * Что роль делает, качество за сутки, параметры и бюджет открываются в боковой панели по клику.
 */
defineProps<{ selected: string | null }>()
const emit = defineEmits<{ select: [role: string] }>()
const store = useRoleStore()
const s = computed(() => store.snapshot)
const sum = computed(() => (s.value ? summary(s.value) : null))
const grouped = computed(() => (s.value ? groups(s.value) : []))
const issues = computed(() => (s.value?.roles ?? []).flatMap((r) => issuesOf(r, s.value?.mode ?? null).map((i) => ({ ...i, role: r.role }))))
const TOP = 2
const allIssues = ref(false)
const shown = computed(() => (allIssues.value ? issues.value : issues.value.slice(0, TOP)))
/** Роли с замечаниями: в строке пометка, чтобы не искать их в списке замечаний. */
const flagged = computed(() => new Set(issues.value.map((i) => i.role)))
const errors = computed(() => s.value?.errors ?? [])
</script>

<template>
  <section class="board" aria-label="Кто думает">
    <header class="top">
      <h2 class="title">Кто думает</h2>
      <template v-if="s">
        <p class="line">{{ sum?.line }}</p>
        <p class="detail">{{ sum?.detail }}</p>
      </template>
    </header>

    <template v-if="s">
      <ul v-if="issues.length" class="issues" aria-label="Замечания">
        <li v-for="(i, k) in shown" :key="k" :class="i.tone"><b>{{ roleLabel(i.role) }}:</b> {{ i.text }}</li>
        <li v-if="issues.length > TOP" class="more-row"><button type="button" class="link" :aria-expanded="allIssues" @click="allIssues = !allIssues">{{ allIssues ? 'Свернуть замечания' : `Показать все замечания (${issues.length})` }}</button></li>
      </ul>
      <details v-if="errors.length" class="tech">
        <summary>Замечания к настройкам моделей ({{ errors.length }})</summary>
        <p v-for="(e, k) in errors" :key="k">{{ remarkText(e) }}</p>
      </details>

      <section v-for="g in grouped" :key="g.id" class="group">
        <h3 class="cap">{{ g.title }}</h3>
        <ul class="roles">
          <li v-for="r in g.roles" :key="r.role">
            <button type="button" class="row" :class="{ sel: selected === r.role }" :aria-pressed="selected === r.role" @click="emit('select', r.role)">
              <span class="who">
                <b>{{ roleLabel(r.role) }}</b>
                <UiChip v-if="r.live_risk" title="От этой роли зависят решения при торговле на реальные деньги">важна для реальных денег</UiChip>
              </span>
              <span class="model">
                <b class="mn">{{ r.primary?.model_name ?? 'модель не назначена' }}</b>
                <span class="place">{{ placeOf(r.primary) }}<template v-if="r.primary && !r.primary.provider_enabled"> · провайдер выключен</template><template v-if="r.fallback.length"> · есть запасная</template><template v-else> · запасной нет</template></span>
              </span>
              <span v-if="flagged.has(r.role)" class="flag"><span class="dot" aria-hidden="true" />есть замечания</span>
              <svg class="chev" viewBox="0 0 12 12" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4.5 3L7.5 6L4.5 9" /></svg>
            </button>
          </li>
        </ul>
      </section>
      <p v-if="!grouped.length" class="empty">Роли мозга не настроены.</p>
    </template>
    <div v-else class="skel" aria-busy="true" />
  </section>
</template>

<style scoped>
.board { display: grid; gap: var(--space-4); }
.top { display: grid; gap: 2px; }
.title { margin: 0 0 var(--space-2); font-size: 16px; font-weight: 700; letter-spacing: -0.01em; }
.line { margin: 0; font-size: 15px; font-weight: 600; line-height: 1.4; }
.detail { margin: 0; font-size: 12px; color: var(--muted); }
.issues { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; font-size: 12px; }
.issues li { display: flex; gap: var(--space-2); color: var(--muted); line-height: 1.45; }
.issues li:not(.more-row)::before { content: ''; flex: none; width: 8px; height: 8px; margin-top: 5px; border-radius: 50%; background: var(--warn); }
.issues li.bad::before { background: var(--neg); }
.issues b { color: var(--text); }
.link { padding: 4px 0; background: none; border: 0; color: var(--muted); font: inherit; font-size: 12px; text-decoration: underline; text-underline-offset: 3px; cursor: pointer; }
.link:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 2px; }
.tech { font-size: 12px; color: var(--muted); }
.tech summary { cursor: pointer; min-height: 28px; display: flex; align-items: center; }
.tech summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 2px; }
.tech p { margin: 4px 0 0; color: var(--neg); overflow-wrap: anywhere; }
.group { display: grid; gap: var(--space-2); }
.cap { margin: 0; font-size: 12px; font-weight: 600; color: var(--muted); }
.roles { list-style: none; margin: 0; padding: 0; background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); overflow: hidden; }
.roles li + li { border-top: 1px solid var(--line); }
.row { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.1fr) 120px 12px; gap: var(--space-4); align-items: center; width: 100%; min-height: 56px; padding: var(--space-3) var(--space-4); background: none; border: 0; color: inherit; font: inherit; text-align: left; cursor: pointer; transition: background-color var(--dur-fast) ease; }
.row.sel { background: var(--panel2); }
.row:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.who { display: flex; flex-wrap: wrap; align-items: center; gap: 4px var(--space-2); font-size: 14px; }
.model { display: grid; gap: 2px; font-size: 12px; min-width: 0; }
.mn { font-size: 13px; overflow-wrap: anywhere; }
.place { color: var(--muted); }
.flag { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: var(--warn); white-space: nowrap; }
.flag .dot { width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
.chev { color: var(--muted); }
.empty { margin: 0; font-size: 13px; color: var(--muted); }
.skel { height: 240px; border-radius: var(--radius); background: var(--panel); }
@media (max-width: 900px) {
  .row { grid-template-columns: minmax(0, 1fr) 12px; row-gap: 4px; }
  .row .model, .row .flag { grid-column: 1; }
  .row .chev { grid-column: 2; grid-row: 1; }
}
@media (hover: hover) and (pointer: fine) {
  .row:not(.sel):hover { background: var(--panel2); }
  .link:hover { color: var(--text); }
}
</style>
