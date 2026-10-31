<script setup lang="ts">
import { computed } from 'vue'
import { ROLE_INFO, issuesOf, pct, placeOf, roleLabel, useRoleStore, type RoleModel } from '@/entities/role'
import { UiChip, UiDetail } from '@/shared/ui'
import { fixed } from '@/shared/lib'

/**
 * Боковая панель роли: что она делает, какая модель основная и какие запасные (где работают, параметры, допуск к live), качество за сутки и расход. Изменить модель здесь пока нельзя:
 * изменения появятся после входа по токену, подсказка об этом на месте кнопки. Технические идентификаторы профилей внизу.
 */
const props = defineProps<{ role: string }>()
defineEmits<{ close: [] }>()
const store = useRoleStore()
const r = computed(() => store.snapshot?.roles.find((x) => x.role === props.role) ?? null)
const issues = computed(() => (r.value ? issuesOf(r.value, store.snapshot?.mode ?? null) : []))
const thinking: Record<string, string> = { off: 'выключено', low: 'немного', medium: 'средне', high: 'много' }
const params = (m: RoleModel) => [
  m.params.temperature != null ? `температура ${m.params.temperature}` : null,
  m.params.max_out_tok != null ? `ответ до ${m.params.max_out_tok} токенов` : null,
  m.params.thinking ? `рассуждение: ${thinking[m.params.thinking] ?? m.params.thinking}` : null,
].filter(Boolean).join(' · ')
const ms = (x: number | null | undefined) => (x == null ? '–' : x >= 1000 ? `${fixed(x / 1000, 1)} с` : `${x} мс`)
</script>

<template>
  <UiDetail :label="`Роль ${roleLabel(role)}`" @close="$emit('close')">
    <template #title>
      <b class="h">{{ roleLabel(role) }}</b>
      <UiChip v-if="r?.live_risk">влияет на live</UiChip>
    </template>

    <template v-if="r">
      <p class="what">{{ ROLE_INFO[role]?.what ?? '' }}</p>

      <ul v-if="issues.length" class="issues"><li v-for="(i, k) in issues" :key="k" :class="i.tone">{{ i.text }}</li></ul>

      <section class="sec">
        <h4>Основная модель</h4>
        <div v-if="r.primary" class="model">
          <b>{{ r.primary.model_name }}</b>
          <p class="meta">{{ placeOf(r.primary) }} · провайдер {{ r.primary.provider }}{{ r.primary.provider_enabled ? '' : ' (выключен)' }} · {{ r.primary.live_ok ? 'допущена к live' : 'не допущена к live' }}</p>
          <p class="meta">{{ params(r.primary) }}</p>
        </div>
        <p v-else class="meta">Модель не назначена.</p>
      </section>

      <section class="sec">
        <h4>Запасные модели</h4>
        <p class="meta">Если основная не ответила, мозг пробует их по очереди.</p>
        <ul v-if="r.fallback.length" class="fbs">
          <li v-for="(f, i) in r.fallback" :key="f.id">
            <b>{{ i + 1 }}. {{ f.model_name }}</b>
            <p class="meta" :class="{ warn: !f.provider_enabled }">{{ placeOf(f) }} · провайдер {{ f.provider }}{{ f.provider_enabled ? '' : ' выключен: запасная не сработает' }}</p>
            <p class="meta">{{ params(f) }}</p>
          </li>
        </ul>
        <p v-else class="meta">Запасных нет.</p>
      </section>

      <section class="sec">
        <h4>За сутки</h4>
        <dl class="kv">
          <div><dt>Вызовов</dt><dd>{{ r.day.calls ?? 0 }}</dd></div>
          <div><dt>Ошибки</dt><dd>{{ pct(r.day.err_rate) }}</dd></div>
          <div><dt>Ответ не принят по формату</dt><dd>{{ pct(r.day.fmt_err_rate) }}</dd></div>
          <div><dt>Переключений на запасную</dt><dd>{{ r.day.fallbacks ?? 0 }}</dd></div>
          <div><dt>Типичное время ответа</dt><dd>{{ ms(r.day.p50_ms) }}</dd></div>
          <div><dt>Стоимость</dt><dd>{{ fixed(r.day.usd ?? 0, 4) }} USD</dd></div>
          <div v-if="r.budget_day_usd != null"><dt>Бюджет в день</dt><dd>{{ fixed(r.budget_day_usd, 2) }} USD, осталось {{ fixed(r.today.budget_left_usd, 2) }}</dd></div>
        </dl>
      </section>

      <p class="locked">Изменить модель роли здесь пока нельзя: правка появится после входа по токену. Сейчас это делается в старом дашборде или командой <code>./tb brain role</code>.</p>

      <p class="tech">профиль {{ r.primary?.id ?? '–' }} · роль {{ r.role }} · {{ r.scope }}{{ r.team ? ' ' + r.team : '' }} · {{ r.io ?? '' }}</p>
    </template>
    <p v-else class="meta">Роль не найдена.</p>
  </UiDetail>
</template>

<style scoped>
.h { font-size: 17px; }
.what { margin: 0; font-size: 13px; line-height: 1.5; }
.issues { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; font-size: 12px; }
.issues li { display: flex; gap: var(--space-2); padding: var(--space-2) var(--space-3); border-radius: var(--radius-sm); background: var(--panel2); color: var(--muted); }
.issues li::before { content: ''; flex: none; width: 8px; height: 8px; margin-top: 5px; border-radius: 50%; background: var(--warn); }
.issues li.bad::before { background: var(--neg); }
.sec { display: grid; gap: var(--space-2); }
.sec h4 { margin: 0; font-size: 12px; font-weight: 600; color: var(--muted); }
.model b, .fbs b { font-size: 14px; overflow-wrap: anywhere; }
.meta { margin: 2px 0 0; font-size: 12px; color: var(--muted); line-height: 1.5; }
.meta.warn { color: var(--warn); }
.fbs { list-style: none; margin: 0; padding: 0; display: grid; gap: var(--space-3); }
.kv { margin: 0; display: grid; gap: 6px; font-size: 12px; }
.kv > div { display: flex; justify-content: space-between; gap: var(--space-4); }
dt { color: var(--muted); }
dd { margin: 0; text-align: right; font-variant-numeric: tabular-nums; }
.locked { margin: 0; padding: var(--space-3); border-radius: var(--radius-sm); background: var(--panel2); font-size: 12px; color: var(--muted); line-height: 1.5; }
.tech { margin: 0; font-size: 11px; color: var(--muted); font-family: var(--mono, ui-monospace, monospace); overflow-wrap: anywhere; }
</style>
