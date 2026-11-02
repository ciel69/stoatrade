<script setup lang="ts">
import { computed } from 'vue'
import { UiChip, UiRouteStrip, type StripBand, type StripMark } from '@/shared/ui'
import { duration, fixed, price } from '@/shared/lib'
import { ideaRisk, ideaRoute, levelWait } from '../lib/idea'
import type { IdeaItem } from '../model/types'

/**
 * Идея, которая ждёт цену у уровня: тот же язык, что у плана. Полоса от стопа к тейку, уровень акцентом с ценой над ним, цена белой меткой, между ними синий отрезок «осталось дойти».
 * В заголовке процент до уровня (зелёный у уровня) и срок идеи; слов одна строка: тезис. Подробности (замеры, пометки, идентификатор) открываются боковой панелью по клику.
 */
const props = defineProps<{ idea: IdeaItem; now: number; selected: boolean }>()
const emit = defineEmits<{ select: [] }>()

const r = computed(() => ideaRoute(props.idea))
const k = computed(() => ideaRisk(props.idea))
const wait = computed(() => levelWait(props.idea))
const sg = computed(() => (props.idea.side === 'short' ? -1 : 1))
/** Смещение цены от уровня в пользу идеи, %. */
const fromLevel = (p: number | null | undefined) => (p != null && props.idea.level ? (sg.value * (p - props.idea.level) / props.idea.level) * 100 : null)
const marks = computed<StripMark[]>(() => {
  const x = r.value
  if (!x) return []
  const i = props.idea
  const out: StripMark[] = [
    { key: 'stop', kind: 'stop', at: 0, name: 'Стоп', price: i.stop, pct: fromLevel(i.stop) },
    { key: 'level', kind: 'level', at: x.level, name: 'Уровень', price: i.level },
    { key: 'take', kind: 'take', at: 1, name: 'Цель', price: i.tp ?? null, pct: fromLevel(i.tp) },
  ]
  if (x.price != null) out.push({ key: 'now', kind: 'now', at: x.price, name: 'Цена', price: i.now_price, pct: fromLevel(i.now_price) })
  return out
})
const bands = computed<StripBand[]>(() => {
  const x = r.value
  if (!x) return []
  const out: StripBand[] = [{ from: 0, to: x.level, kind: 'risk' }]
  if (x.gap) out.push({ from: x.gap[0], to: x.gap[1], kind: 'gap' })
  return out
})
const left = computed(() => (props.idea.expires_epoch != null ? props.idea.expires_epoch - props.now : null))
const dist = computed(() => (props.idea.dist_level_pct != null ? `${props.idea.dist_level_pct > 0 ? '' : '−'}${fixed(Math.abs(props.idea.dist_level_pct))}%` : '–'))
const tip = computed(() => [props.idea.tag, props.idea.note, k.value.rr != null ? `RR ${fixed(k.value.rr, 1)} без издержек` : ''].filter(Boolean).join(' · '))
</script>

<template>
  <li class="card" :class="{ near: idea.zone, sel: selected }">
    <button type="button" class="hit" :aria-pressed="selected" :aria-label="`${idea.sym}: открыть идею`" :title="tip" @click="emit('select')" />
    <div class="body">
      <span class="head">
        <span class="sym">{{ idea.sym }}</span>
        <UiChip>{{ idea.side === 'long' ? 'лонг' : idea.side === 'short' ? 'шорт' : '?' }}</UiChip>
        <UiChip v-if="idea.tag">{{ idea.tag }}</UiChip>
        <span class="dist" :class="`w-${wait.tone}`" :title="wait.text">{{ dist }}</span>
        <span class="ttl" :class="{ soon: left != null && left < 1800 }" :title="left == null ? 'без срока' : 'идея истечёт, если цена не дойдёт до уровня'">{{ left == null ? 'без срока' : left > 0 ? duration(left) : 'срок вышел' }}</span>
      </span>

      <UiRouteStrip v-if="r" class="strip" :marks="marks" :bands="bands" :flag="{ at: r.level, text: price(idea.level), markKey: 'level' }"
                    :label="`${idea.sym}: от стопа ${price(idea.stop)} через уровень ${price(idea.level)} к цели ${price(idea.tp)}, цена ${price(idea.now_price)}`" @click="emit('select')">
        <template #caption="{ active, set }">
          <span class="rs-i" :class="{ hot: active === 'stop' }" @pointerenter="set('stop')" @pointerleave="set(null)">
            <span class="down">{{ price(idea.stop) }}<template v-if="k.risk != null"> −{{ fixed(k.risk) }}%</template></span>
          </span>
          <span class="rs-i rs-end" :class="{ hot: active === 'take' }" @pointerenter="set('take')" @pointerleave="set(null)">
            <span class="up">{{ price(idea.tp) }}<template v-if="k.reward != null"> +{{ fixed(k.reward) }}%</template></span>
          </span>
        </template>
      </UiRouteStrip>
      <span v-else class="note-only">Уровня или стопа нет: сканер по этой заметке мозг не разбудит.</span>

      <span v-if="idea.note" class="note">{{ idea.note }}</span>
    </div>
  </li>
</template>

<style scoped>
/* Вид строки (разделители, поля, выделение) задаёт список, как на странице планов */
.card { position: relative; min-width: 0; padding: var(--space-3) 0; }
/* Вся карточка кликабельна одной кнопкой под содержимым: внутри полосы свои кнопки-деления, вложенные кнопки недопустимы */
.hit { position: absolute; inset: 0; width: 100%; padding: 0; border: 0; background: none; cursor: pointer; border-radius: var(--radius-sm); }
.hit:focus-visible { outline: 2px solid var(--accent); outline-offset: 4px; }
.body { position: relative; pointer-events: none; }
.strip { pointer-events: auto; margin-top: var(--space-2); }
.head { display: flex; align-items: center; gap: var(--space-2); }
.sym { font-size: 17px; font-weight: 700; letter-spacing: -0.01em; }
.dist { margin-left: auto; font-size: 17px; font-weight: 700; font-variant-numeric: tabular-nums; color: var(--muted); }
.dist.w-near { color: var(--pos); }
.dist.w-close { color: var(--text); }
.ttl { min-width: 64px; text-align: right; font-size: 12px; font-variant-numeric: tabular-nums; color: var(--muted); }
.ttl.soon { color: var(--warn); }
.up { color: var(--pos); }
.down { color: var(--neg); }
.note-only { display: block; margin-top: 2px; font-size: 12px; color: var(--muted); }
.note { display: block; margin-top: var(--space-1); font-size: 12px; color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
@media (hover: hover) and (pointer: fine) {
  .card:hover .sym { color: var(--accent); }
}
</style>
