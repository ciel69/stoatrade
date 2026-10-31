<script setup lang="ts">
import { computed } from 'vue'
import { UiChip } from '@/shared/ui'
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
const units = (x: number) => x * 100
const levelLeft = computed(() => (r.value ? Math.min(90, Math.max(10, units(r.value.level))) : 50))
const left = computed(() => (props.idea.expires_epoch != null ? props.idea.expires_epoch - props.now : null))
const dist = computed(() => (props.idea.dist_level_pct != null ? `${props.idea.dist_level_pct > 0 ? '' : '−'}${fixed(Math.abs(props.idea.dist_level_pct))}%` : '–'))
const tip = computed(() => [props.idea.tag, props.idea.note, k.value.rr != null ? `RR ${fixed(k.value.rr, 1)} без издержек` : ''].filter(Boolean).join(' · '))
</script>

<template>
  <li class="card" :class="{ near: idea.zone, sel: selected }">
    <button type="button" class="hit" :aria-pressed="selected" :title="tip" @click="emit('select')">
      <span class="head">
        <span class="sym">{{ idea.sym }}</span>
        <UiChip>{{ idea.side === 'long' ? 'лонг' : idea.side === 'short' ? 'шорт' : '?' }}</UiChip>
        <UiChip v-if="idea.tag">{{ idea.tag }}</UiChip>
        <span class="dist" :class="`w-${wait.tone}`" :title="wait.text">{{ dist }}</span>
        <span class="ttl" :class="{ soon: left != null && left < 1800 }" :title="left == null ? 'без срока' : 'идея истечёт, если цена не дойдёт до уровня'">{{ left == null ? 'без срока' : left > 0 ? duration(left) : 'срок вышел' }}</span>
      </span>

      <span v-if="r" class="viz">
        <span class="lvl" :style="{ left: levelLeft + '%' }">{{ price(idea.level) }}</span>
        <svg class="route" viewBox="0 0 100 14" preserveAspectRatio="none" role="img"
             :aria-label="`${idea.sym}: от стопа ${price(idea.stop)} через уровень ${price(idea.level)} к цели ${price(idea.tp)}, цена ${price(idea.now_price)}`">
          <rect class="track" x="0" y="6" width="100" height="2" />
          <rect class="risk" x="0" y="4.5" :width="units(r.level)" height="5" />
          <rect v-if="r.gap" class="gap" :x="units(r.gap[0])" y="4.5" :width="units(r.gap[1] - r.gap[0])" height="5" />
          <line class="take" x1="100" x2="100" y1="2" y2="12" />
          <line class="entry" :x1="units(r.level)" :x2="units(r.level)" y1="0" y2="14" />
          <line class="stop" x1="0" x2="0" y1="0" y2="14" />
          <line v-if="r.price != null" class="now" x1="0" x2="0" y1="0" y2="14" :style="{ transform: `translateX(${units(r.price)}px)` }" />
        </svg>
        <span class="ends">
          <span class="down">{{ price(idea.stop) }}<template v-if="k.risk != null"> −{{ fixed(k.risk) }}%</template></span>
          <span class="up">{{ price(idea.tp) }}<template v-if="k.reward != null"> +{{ fixed(k.reward) }}%</template></span>
        </span>
      </span>
      <span v-else class="note-only">Уровня или стопа нет: сканер по этой заметке мозг не разбудит.</span>

      <span v-if="idea.note" class="note">{{ idea.note }}</span>
    </button>
  </li>
</template>

<style scoped>
.card { padding: var(--space-3) 0; border-top: 1px solid var(--line); }
.card:first-child { border-top: 0; padding-top: 0; }
.card.sel { box-shadow: inset 3px 0 0 var(--accent); padding-left: var(--space-3); }
.hit { display: block; width: 100%; padding: 0; border: 0; background: none; color: inherit; font: inherit; text-align: left; cursor: pointer; border-radius: var(--radius-sm); }
.hit:focus-visible { outline: 2px solid var(--accent); outline-offset: 4px; }
.head { display: flex; align-items: center; gap: var(--space-2); }
.sym { font-size: 17px; font-weight: 700; letter-spacing: -0.01em; }
.dist { margin-left: auto; font-size: 17px; font-weight: 700; font-variant-numeric: tabular-nums; color: var(--muted); }
.dist.w-near { color: var(--pos); }
.dist.w-close { color: var(--text); }
.ttl { min-width: 64px; text-align: right; font-size: 12px; font-variant-numeric: tabular-nums; color: var(--muted); }
.ttl.soon { color: var(--warn); }
.viz { display: block; position: relative; padding-top: 14px; margin-top: var(--space-1); }
.lvl { position: absolute; top: 0; transform: translateX(-50%); font-size: 11px; font-weight: 600; color: var(--accent); font-variant-numeric: tabular-nums; white-space: nowrap; }
.route { display: block; width: 100%; height: 18px; overflow: visible; }
.track { fill: var(--line2); }
.risk { fill: var(--neg); opacity: 0.22; }
.gap { fill: var(--accent); opacity: 0.5; }
.take { stroke: var(--pos); stroke-width: 1.4; vector-effect: non-scaling-stroke; opacity: 0.8; }
.entry { stroke: var(--accent); stroke-width: 2; vector-effect: non-scaling-stroke; }
.stop { stroke: var(--neg); stroke-width: 2; vector-effect: non-scaling-stroke; }
.now { stroke: var(--text); stroke-width: 2.4; vector-effect: non-scaling-stroke; transition: transform 400ms var(--ease-out); }
.ends { display: flex; justify-content: space-between; margin-top: 3px; font-size: 11px; font-variant-numeric: tabular-nums; }
.up { color: var(--pos); }
.down { color: var(--neg); }
.note-only { display: block; margin-top: 2px; font-size: 12px; color: var(--muted); }
.note { display: block; margin-top: var(--space-1); font-size: 12px; color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
@media (hover: hover) and (pointer: fine) {
  .hit:hover .sym { color: var(--accent); }
}
@media (prefers-reduced-motion: reduce) {
  .now { transition: none; }
}
</style>
