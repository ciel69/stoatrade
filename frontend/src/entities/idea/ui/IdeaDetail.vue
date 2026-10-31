<script setup lang="ts">
import { computed } from 'vue'
import { fixed, price, signed } from '@/shared/lib'
import { outcomeList } from '../lib/idea'
import type { IdeaItem } from '../model/types'

/** Раскрытые подробности идеи: тезис, цены, «что было бы» по горизонтам (до и после издержек), связанные план и сделка, пометки. */
const props = defineProps<{ idea: IdeaItem }>()
const outs = computed(() => outcomeList(props.idea.outcomes))
const tone = (v: number | null) => (v == null || v === 0 ? '' : v > 0 ? 'up' : 'down')
const tradeText = computed(() => {
  const t = props.idea.trade
  if (t) return `${t.id} · ${t.status === 'open' ? 'открыта' : (t.reason ?? 'закрыта')}`
  return props.idea.trade_id ? `${props.idea.trade_id} не найдена в журнале` : null
})
</script>

<template>
  <div class="det">
    <p v-if="idea.note" class="note">{{ idea.note }}</p>
    <dl class="kv">
      <div v-if="idea.level != null"><dt>Уровень</dt><dd>{{ price(idea.level) }}<template v-if="idea.dist_level_pct != null"> · цена {{ signed(idea.dist_level_pct) }}% от уровня</template></dd></div>
      <div v-if="idea.stop != null || idea.tp != null"><dt>Стоп / тейк</dt><dd>{{ price(idea.stop) }} / {{ price(idea.tp) }}</dd></div>
      <div v-if="idea.price != null"><dt>Цена записи</dt><dd>{{ price(idea.price) }}<template v-if="idea.now_price != null"> → {{ price(idea.now_price) }}<template v-if="idea.shift_pct != null"> ({{ signed(idea.shift_pct) }}%)</template></template></dd></div>
      <div v-if="idea.plan_id"><dt>План</dt><dd class="mono">{{ idea.plan_id }}</dd></div>
      <div v-if="tradeText"><dt>Сделка</dt><dd>{{ tradeText }}</dd></div>
      <div v-if="idea.tag"><dt>Тег</dt><dd>{{ idea.tag }}</dd></div>
    </dl>
    <table v-if="outs.length" class="what" aria-label="Что было бы по стороне идеи">
      <thead><tr><th>Через</th><th>Ход по стороне</th><th>После издержек</th></tr></thead>
      <tbody>
        <tr v-for="o in outs" :key="o.h">
          <td>{{ o.h }} мин</td>
          <td v-if="o.dir_pct != null" :class="tone(o.dir_pct)">{{ signed(o.dir_pct) }}%</td>
          <td v-else colspan="2" class="muted">{{ signed(o.ret_pct) }}% (сторона не задана)</td>
          <td v-if="o.dir_pct != null" :class="tone(o.net_pct)">{{ signed(o.net_pct) }}%</td>
        </tr>
      </tbody>
    </table>
    <p v-else-if="idea.status === 'watching' || idea.status === 'idea'" class="muted">Замеры после +15 и +60 минут появятся, когда идея сработает или истечёт.</p>
    <p v-if="idea.mark_notes.length" class="muted">Пометки: {{ idea.mark_notes.join('; ') }}</p>
    <p class="muted mono id">{{ idea.id }}<template v-if="idea.trade && idea.trade.r != null"> · {{ fixed(idea.trade.r, 1) }} R</template></p>
  </div>
</template>

<style scoped>
.det { margin-top: var(--space-3); padding: var(--space-3); border-radius: var(--radius-sm); background: var(--panel2); display: grid; gap: var(--space-2); font-size: 12px; }
.note { margin: 0; color: var(--text); font-size: 13px; line-height: 1.45; }
.kv { display: grid; gap: 4px; margin: 0; }
.kv > div { display: flex; gap: var(--space-3); }
dt { flex: none; width: 110px; color: var(--muted); }
dd { margin: 0; min-width: 0; overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }
.what { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
.what th { text-align: left; font-weight: 400; color: var(--muted); padding: 2px 0; }
.what td { padding: 2px 0; }
.up { color: var(--pos); }
.down { color: var(--neg); }
.muted { margin: 0; color: var(--muted); }
.mono { font-family: var(--mono, ui-monospace, monospace); font-size: 11px; }
.id { overflow-wrap: anywhere; }
</style>
