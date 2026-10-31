<script setup lang="ts">
import { ref, watch } from 'vue'
import { LIMIT_FIELDS, limitInput } from '@/entities/role'
import { useActionStore } from '@/entities/action'
import { useRefocus } from '@/shared/lib'
import { waitText, type ProviderEdit } from '../model/useProviderEdit'

/**
 * Потолки расхода: пять полей, пустое поле или 0 значит «без потолка». Правки копятся черновиком со строками «было → станет»,
 * «Применить» запускает одно действие со всеми пятью значениями (подтверждение рисков по правилам сервера). Ошибка ввода стоит у поля и блокирует «Применить».
 */
const props = defineProps<{ edit: ProviderEdit }>()
const e = props.edit
const action = useActionStore()
const first = ref<HTMLInputElement | null>(null)
const bar = ref<HTMLElement | null>(null)

// После «Применить» и «Сбросить» панель изменений исчезает вместе с кнопкой: фокус возвращается на первое поле.
const refocus = useRefocus({
  blocked: () => action.riskyRun === true && action.phase !== 'idle',
  target: () => first.value,
  lostIn: () => bar.value,
})
function reset(): void {
  e.resetLimits()
  refocus.arm()
}
const apply = (): void => {
  refocus.mark()
  e.submitLimits()
}
// успех действия регистрирует ожидание отражения: черновик очищен, панель изменений исчезла
watch(() => e.waitOf('limits')?.id, (w) => { if (w) refocus.arm() })
const id = (k: string): string => `pe-lim-${k}`
</script>

<template>
  <section class="pe-sec" aria-labelledby="pe-lim-h">
    <h4 id="pe-lim-h">Потолки расхода</h4>
    <p class="pe-meta">Когда потолок исчерпан, вызовы к провайдеру останавливаются до конца часа или суток.</p>
    <form class="grid" novalidate @submit.prevent="apply" @keydown.enter.prevent="apply">
      <div v-for="(f, i) in LIMIT_FIELDS" :key="f.key" class="fld">
        <label :for="id(f.key)">{{ f.label }}<span class="un">{{ f.unit }}</span></label>
        <input
          :id="id(f.key)" :ref="(el) => { if (i === 0) first = el as HTMLInputElement | null }" class="pe-input" type="text" :inputmode="f.kind === 'usd' ? 'decimal' : 'numeric'"
          autocomplete="off" spellcheck="false" placeholder="без потолка" :disabled="e.busy.value"
          :value="e.text(f.key, limitInput(e.shownLimits.value[f.key]))" :aria-invalid="!!e.errors.value[f.key]" :aria-describedby="id(f.key) + '-h'"
          @input="e.setField(f.key, ($event.target as HTMLInputElement).value)"
        >
        <p :id="id(f.key) + '-h'" class="pe-meta" :class="{ bad: !!e.errors.value[f.key] }">{{ e.errors.value[f.key] ?? '0 или пусто: без потолка' }}</p>
      </div>
    </form>
    <p class="pe-meta" aria-live="polite">{{ e.waitOf('limits') ? waitText(e.waitOf('limits')!) : '' }}</p>

    <Transition name="pe">
      <div v-if="e.lines.value.length || e.hasErrors.value" ref="bar" class="bar" role="group" aria-label="Изменения потолков расхода">
        <dl v-if="e.lines.value.length" class="chg">
          <div v-for="l in e.lines.value" :key="l.key"><dt>{{ l.label }}</dt><dd>{{ l.from }} <span class="ar">→</span> <b>{{ l.to }}</b></dd></div>
        </dl>
        <p v-if="e.hasErrors.value" class="pe-meta bad">Исправьте поля с ошибкой: пока они есть, применить нельзя.</p>
        <div class="pe-acts">
          <button type="button" class="pe-ghost" :disabled="e.busy.value" @click="reset">Сбросить</button>
          <button type="button" class="pe-cta" :disabled="e.busy.value || e.hasErrors.value || !e.lines.value.length" @click="apply">Применить</button>
        </div>
      </div>
    </Transition>
  </section>
</template>

<style scoped>
.grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--space-3) var(--space-3); }
.fld { display: grid; gap: 4px; align-content: start; min-width: 0; }
label { display: flex; justify-content: space-between; gap: var(--space-2); font-size: 12px; color: var(--text); }
.un { color: var(--muted); }
.fld .pe-meta { margin: 0; min-height: 18px; }
.bar { display: grid; gap: var(--space-3); padding-top: var(--space-3); border-top: 1px solid var(--line2); }
.chg { margin: 0; display: grid; gap: 4px; font-size: 12px; }
.chg > div { display: grid; grid-template-columns: 150px minmax(0, 1fr); gap: var(--space-2); }
.chg dt { color: var(--muted); }
.chg dd { margin: 0; text-align: left; overflow-wrap: anywhere; }
.chg b { font-weight: 650; }
.ar { color: var(--muted); }
@media (max-width: 420px) {
  .grid { grid-template-columns: minmax(0, 1fr); }
}
</style>
