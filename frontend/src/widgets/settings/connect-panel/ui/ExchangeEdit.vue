<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useActionStore } from '@/entities/action'
import { EXCHANGE_MODES, modeText } from '@/entities/role'
import { UiChangeBar } from '@/shared/ui'
import { reflectText, useRefocus } from '@/shared/lib'
import type { ExchangeEdit } from '../model/useExchangeEdit'
import './provider-edit.css'

/**
 * Правка биржи: «Основная биржа» (сделать основной) и «Режим биржи». Биржи меняются только при остановленной платформе: иначе элементы выключены, причина написана словами.
 * Выбор показывает «было → станет» и последствия; «Применить» запускает общее действие, живые деньги откроют единый диалог «Принимаю риски».
 */
const props = defineProps<{ edit: ExchangeEdit }>()
const action = useActionStore()
const e = props.edit
const r = computed(() => e.row.value)
const off = computed(() => e.busy.value || !!e.platformWhy.value)
const hint = computed(() => EXCHANGE_MODES.find((m) => m.value === r.value?.mode)?.hint ?? '')

const root = ref<HTMLElement | null>(null)
const actBar = ref<{ $el: HTMLElement } | null>(null)
const modeBar = ref<{ $el: HTMLElement } | null>(null)
const blocked = (): boolean => action.riskyRun === true && action.phase !== 'idle'
const actFocus = useRefocus({ blocked, target: () => root.value?.querySelector<HTMLElement>('[data-act]'), fallback: () => root.value, lostIn: () => actBar.value?.$el })
const modeFocus = useRefocus({ blocked, target: () => root.value?.querySelector<HTMLElement>('.pe-modes button[aria-pressed="true"]'), fallback: () => root.value, lostIn: () => modeBar.value?.$el })
watch(e.activateChange, (v, o) => { if (!v && o) actFocus.arm() })
watch(e.modeChange, (v, o) => { if (!v && o) modeFocus.arm() })
function applyActivate(): void { actFocus.mark(); e.submitActivate() }
function applyMode(): void { modeFocus.mark(); e.submitMode() }
</script>

<template>
  <div v-if="r" ref="root" class="ee" tabindex="-1">
    <p v-if="e.platformWhy.value" class="pe-locked">{{ e.platformWhy.value }}</p>

    <section class="pe-sec">
      <h4>Основная биржа</h4>
      <div class="row">
        <p class="pe-meta">{{ r.active ? 'Платформа торгует на этой бирже.' : (e.activateWhy.value ?? `Сейчас торгует ${e.current.value?.name ?? 'ни одна биржа'}. Можно сделать основной эту.`) }}</p>
        <button v-if="!r.active" type="button" class="pe-btn" data-act :aria-pressed="e.activateDraft.value" :disabled="off || !!e.activateWhy.value" @click="e.toggleActivate()">Сделать основной</button>
      </div>
      <Transition name="pe">
        <UiChangeBar v-if="e.activateChange.value" ref="actBar" :line="e.activateChange.value.line" :effect="e.activateChange.value.effect" apply="Сделать основной" :busy="e.busy.value" @apply="applyActivate" @reset="e.resetActivate()" />
      </Transition>
    </section>

    <section class="pe-sec">
      <h4>Режим биржи</h4>
      <p class="pe-meta">Сейчас: {{ modeText(r.mode) }}. {{ hint }}</p>
      <div class="pe-modes" role="group" aria-label="Режим биржи">
        <button v-for="m in EXCHANGE_MODES" :key="m.value" type="button" class="pe-btn mode" :class="{ pick: m.value === e.modeDraft.value }" :aria-pressed="m.value === (e.modeDraft.value ?? r.mode)" :disabled="off" @click="e.pickMode(m.value)">{{ m.label }}</button>
      </div>
      <Transition name="pe">
        <UiChangeBar v-if="e.modeChange.value" ref="modeBar" :line="e.modeChange.value.line" :effect="e.modeChange.value.effect" :hint="e.modeChange.value.hint" apply="Применить режим" :busy="e.busy.value" @apply="applyMode" @reset="e.resetMode()" />
      </Transition>
    </section>

    <p class="pe-meta" role="status" aria-live="polite">{{ reflectText(e.wait.state.value) }}</p>
  </div>
</template>

<style scoped>
.ee { display: grid; gap: var(--space-4); outline: none; }
.row { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); }
.row .pe-meta { flex: 1 1 auto; min-width: 0; }
.pe-modes { display: flex; flex-wrap: wrap; gap: var(--space-2); }
.mode { min-height: 40px; padding: 0 var(--space-3); font-size: 13px; }
.mode[aria-pressed='true'] { background: var(--panel2); border-color: var(--line2); }
.mode.pick { background: var(--accent-bg); border-color: var(--accent); }
[aria-live]:empty { display: none; }
</style>
