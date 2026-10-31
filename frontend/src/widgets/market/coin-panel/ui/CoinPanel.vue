<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { coinKey, kfmt, useMarketStore } from '@/entities/market'
import { useIdeaStore } from '@/entities/idea'
import { useTradingStore } from '@/entities/trading'
import { UiChip } from '@/shared/ui'
import { fixed, price, signed } from '@/shared/lib'

/**
 * Панель выбранной монеты рядом с картой: цена и ход, метрики, карта ликвидаций за сутки (где выбивали лонги и шорты), ордерфлоу и стены стакана (у монет с живым потоком),
 * сведения сканера, фид и планы и идеи мозга по этой монете. Esc или крестик закрывают.
 */
const props = defineProps<{ sym: string }>()
const emit = defineEmits<{ close: [] }>()
const market = useMarketStore()
const trading = useTradingStore()
const ideas = useIdeaStore()
const root = ref<HTMLElement | null>(null)
onMounted(() => root.value?.focus({ preventScroll: true }))

const key = computed(() => coinKey(props.sym))
const heat = computed(() => market.heatBy.get(key.value) ?? null)
const liq = computed(() => market.liqBy.get(key.value) ?? null)
const flow = computed(() => market.flowBy.get(key.value) ?? null)
const hot = computed(() => market.hotBy.get(key.value) ?? null)
const feed = computed(() => market.snapshot?.coins.rows.find((c) => coinKey(c.sym) === key.value) ?? null)
const px = computed(() => heat.value?.price ?? flow.value?.px ?? liq.value?.px ?? hot.value?.price ?? null)
const plans = computed(() => (trading.plans?.items ?? []).filter((p) => coinKey(p.sym) === key.value && p.col !== 'closed'))
const myIdeas = computed(() => (ideas.snapshot?.items ?? []).filter((i) => coinKey(i.sym) === key.value && (i.status === 'watching' || i.status === 'idea' || i.status === 'planned')))

const tone = (v: number | null | undefined) => (v == null || v === 0 ? '' : v > 0 ? 'up' : 'down')
const pct = (v: number | null | undefined, d = 2) => (v == null ? '–' : `${signed(v, d)}%`)
const frac = (v: number | null | undefined, d = 2) => (v == null ? '–' : `${signed(v * 100, d)}%`)

/** Уровни карты ликвидаций: до 22 самых крупных, сверху вниз по цене. */
const levels = computed(() => {
  const m = (liq.value?.map ?? []).slice().sort((a, b) => b.long + b.short - (a.long + a.short)).slice(0, 22).sort((a, b) => b.px - a.px)
  const mx = Math.max(1, ...m.map((r) => Math.max(r.long, r.short)))
  return m.map((r) => ({ ...r, l: (100 * r.long) / mx, s: (100 * r.short) / mx, cur: px.value != null && Math.abs(px.value / r.px - 1) <= 0.0025 }))
})
const book = computed(() => {
  const f = flow.value
  if (!f || f.bid_usd == null || f.ask_usd == null || f.bid_usd + f.ask_usd === 0) return null
  return (100 * f.bid_usd) / (f.bid_usd + f.ask_usd)
})
const regime = computed(() => (feed.value?.regime && feed.value.regime !== 'unknown' ? feed.value.regime : feed.value?.regime_1h ? `${feed.value.regime_1h} · 1ч` : null))
</script>

<template>
  <aside ref="root" class="panel" tabindex="-1" :aria-label="`Монета ${key}`" @keydown.esc="emit('close')">
    <header class="head">
      <h3 class="sym">{{ key }}</h3>
      <span class="px">{{ price(px) }}</span>
      <span v-if="heat" class="chg" :class="tone(heat.chg_24h_pct)">{{ pct(heat.chg_24h_pct) }} за сутки</span>
      <UiChip v-if="heat && !heat.tradable" tone="warn">сканер не допускает</UiChip>
      <button type="button" class="x" aria-label="Закрыть" @click="emit('close')"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg></button>
    </header>

    <div class="body">
      <p v-if="!heat && !flow && !liq && !hot && !feed" class="muted">По этой монете сейчас нет данных: она вне топа карты.</p>

      <dl v-if="heat" class="kv">
        <div><dt>5 мин</dt><dd :class="tone(heat.ret_5m_pct)">{{ pct(heat.ret_5m_pct) }}</dd></div>
        <div><dt>15 мин</dt><dd :class="tone(heat.ret_15m_pct)">{{ pct(heat.ret_15m_pct) }}</dd></div>
        <div><dt>Диапазон 5 мин</dt><dd>{{ fixed(heat.range_5m_pct) }}%</dd></div>
        <div><dt>Оборот к норме</dt><dd>{{ heat.turn_surge_5m != null ? '×' + fixed(heat.turn_surge_5m, 1) : '–' }}</dd></div>
        <div><dt>Откр. интерес 15 мин</dt><dd :class="tone(heat.oi_chg_15m_pct)">{{ pct(heat.oi_chg_15m_pct) }}</dd></div>
        <div><dt>Откр. интерес 1 ч</dt><dd :class="tone(heat.oi_chg_1h_pct)">{{ pct(heat.oi_chg_1h_pct) }}</dd></div>
        <div><dt>Фандинг</dt><dd>{{ pct(heat.funding_pct, 3) }}</dd></div>
        <div><dt>Оборот за сутки</dt><dd>{{ heat.turn_24h_musd != null ? fixed(heat.turn_24h_musd, 0) + ' млн' : '–' }}</dd></div>
        <div><dt>Открытый интерес</dt><dd>{{ heat.oi_musd != null ? fixed(heat.oi_musd, 1) + ' млн' : '–' }}</dd></div>
      </dl>

      <section v-if="plans.length || myIdeas.length" class="sec">
        <h4>План и идеи мозга</h4>
        <ul class="links">
          <li v-for="p in plans" :key="p.id">
            <RouterLink :to="{ path: '/plans', query: { plan: p.id } }"><UiChip tone="accent">план</UiChip> {{ p.side === 'long' ? 'лонг' : 'шорт' }}<template v-if="p.dist_pct != null"> · до входа {{ fixed(p.dist_pct) }}%</template> · {{ p.col === 'in_trade' ? 'в сделке' : p.col === 'waiting' ? 'ждёт входа' : 'далеко' }}</RouterLink>
          </li>
          <li v-for="i in myIdeas" :key="i.id">
            <RouterLink :to="{ path: '/ideas' }"><UiChip>идея</UiChip> {{ i.side === 'long' ? 'лонг' : 'шорт' }}<template v-if="i.level != null"> от {{ price(i.level) }}</template><template v-if="i.dist_level_pct != null"> · цена {{ signed(i.dist_level_pct) }}% от уровня</template></RouterLink>
          </li>
        </ul>
      </section>

      <section class="sec">
        <h4>Ликвидации</h4>
        <template v-if="liq">
          <p class="line">За час: выбиты лонги <b class="down">{{ kfmt(liq.long_1h) }}</b>, шорты <b class="up">{{ kfmt(liq.short_1h) }}</b> USD, событий {{ liq.n_1h }}.</p>
          <ul v-if="levels.length" class="lm" aria-label="Карта ликвидаций за сутки">
            <li v-for="r in levels" :key="r.px" :class="{ cur: r.cur }" :title="`лонги ${kfmt(r.long)} · шорты ${kfmt(r.short)} USD`">
              <span class="lpx">{{ price(r.px) }}</span>
              <span class="bars"><i class="bl" :style="{ width: r.l / 2 + '%' }" /><i class="bs" :style="{ width: r.s / 2 + '%' }" /></span>
              <span class="lv">{{ r.long ? 'L ' + kfmt(r.long) : '' }}{{ r.long && r.short ? ' · ' : '' }}{{ r.short ? 'S ' + kfmt(r.short) : '' }}</span>
            </li>
          </ul>
        </template>
        <p v-else class="muted">Ликвидаций за сутки по монете нет в потоке (или монета вне подписки).</p>
      </section>

      <section v-if="flow" class="sec">
        <h4>Ордерфлоу</h4>
        <dl class="kv">
          <div><dt>Агрессия 5 мин</dt><dd :class="tone(flow.cvd_5m_pct)">{{ pct(flow.cvd_5m_pct, 0) }} <span class="sub">купили {{ kfmt(flow.buy_5m_usd) }} · продали {{ kfmt(flow.sell_5m_usd) }}</span></dd></div>
          <div><dt>Крупные принты</dt><dd>{{ flow.big_5m_n ? `${flow.big_5m_n} шт., баланс ${kfmt(flow.big_5m_usd)} USD` : 'нет' }}</dd></div>
          <div><dt>Binance</dt><dd>{{ flow.xex_bps != null ? (flow.xex_bps >= 0 ? 'Bybit дороже на ' : 'Bybit дешевле на ') + fixed(Math.abs(flow.xex_bps), 1) + ' б.п.' : 'нет данных' }}</dd></div>
          <div><dt>Спред</dt><dd>{{ flow.spread_bps != null ? fixed(flow.spread_bps) + ' б.п.' : '–' }}</dd></div>
        </dl>
        <div v-if="book != null" class="book" :title="`bid ${kfmt(flow.bid_usd)} · ask ${kfmt(flow.ask_usd)} USD`">
          <span class="bk"><i :style="{ width: book + '%' }" /></span>
          <span class="bl-t"><span class="up">bid {{ kfmt(flow.bid_usd) }}</span><span class="down">ask {{ kfmt(flow.ask_usd) }}</span></span>
        </div>
        <ul v-if="flow.walls.length" class="walls">
          <li v-for="w in flow.walls" :key="w.side + w.px"><b :class="w.side === 'bid' ? 'up' : 'down'">{{ w.side === 'bid' ? 'поддержка' : 'сопротивление' }}</b> {{ price(w.px) }} · {{ kfmt(w.usd) }} USD · {{ fixed(w.dist_pct) }}% от цены<span v-if="!w.stable" class="sub"> · может мигать</span></li>
        </ul>
        <p v-else class="muted">Крупных стен в стакане нет.</p>
      </section>

      <section v-if="hot" class="sec">
        <h4>Сканер</h4>
        <dl class="kv">
          <div><dt>Ранг и счёт</dt><dd>#{{ hot.rank ?? '–' }} · {{ fixed(hot.score) }}</dd></div>
          <div><dt>Лот / плечо</dt><dd>{{ hot.lot_usdt != null ? fixed(hot.lot_usdt, 1) + ' USDT' : '–' }} · {{ hot.max_leverage != null ? hot.max_leverage + '×' : '–' }}</dd></div>
          <div><dt>Спред</dt><dd>{{ hot.spread_bps != null ? fixed(hot.spread_bps, 1) + ' б.п.' : '–' }}</dd></div>
        </dl>
        <p v-if="hot.reasons?.length" class="line">Почему в списке: {{ hot.reasons.join('; ') }}</p>
        <p v-if="hot.skip?.length" class="line warn">Ограничения: {{ hot.skip.join('; ') }}</p>
      </section>

      <section v-if="feed" class="sec">
        <h4>Фид цен</h4>
        <dl class="kv">
          <div v-if="regime"><dt>Режим</dt><dd>{{ regime }}</dd></div>
          <div><dt>Доходность 1 / 5 / 15 мин</dt><dd>{{ frac(feed.ret_1m) }} · {{ frac(feed.ret_5m) }} · {{ frac(feed.ret_15m) }}</dd></div>
          <div><dt>Волатильность 5 мин / 1 ч</dt><dd>{{ feed.rv_5m != null ? fixed(feed.rv_5m * 100) + '%' : '–' }} · {{ feed.rv_1h != null ? fixed(feed.rv_1h * 100) + '%' : '–' }}</dd></div>
          <div><dt>Перекос top-5 стакана</dt><dd>{{ feed.imbalance != null ? fixed(feed.imbalance) : '–' }}</dd></div>
          <div><dt>Цены по источникам</dt><dd>{{ Object.entries(feed.prices_by_src).map(([k, v]) => `${k} ${price(v)}`).join(' · ') || '–' }}</dd></div>
        </dl>
      </section>
    </div>
  </aside>
</template>

<style scoped>
.panel { display: flex; flex-direction: column; min-height: 0; background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); outline: none; }
.head { display: flex; align-items: baseline; flex-wrap: wrap; gap: var(--space-2) var(--space-3); padding: var(--space-4) var(--space-4) var(--space-3); border-bottom: 1px solid var(--line); position: relative; padding-right: 52px; }
.sym { margin: 0; font-size: 20px; font-weight: 700; letter-spacing: -0.01em; }
.px { font-size: 16px; font-weight: 600; font-variant-numeric: tabular-nums; }
.chg { font-size: 12px; color: var(--muted); }
.x { position: absolute; top: var(--space-3); right: var(--space-3); display: grid; place-items: center; width: 32px; height: 32px; border: 0; border-radius: var(--radius-sm); background: none; color: var(--muted); cursor: pointer; }
.x:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.body { overflow-y: auto; padding: var(--space-3) var(--space-4) var(--space-4); display: grid; gap: var(--space-4); align-content: start; }
.sec { display: grid; gap: var(--space-2); }
.sec h4 { margin: 0; font-size: 12px; font-weight: 600; color: var(--muted); }
.kv { margin: 0; display: grid; gap: 4px; font-size: 12px; }
.kv > div { display: flex; gap: var(--space-3); justify-content: space-between; }
dt { flex: none; color: var(--muted); }
dd { margin: 0; text-align: right; font-variant-numeric: tabular-nums; min-width: 0; overflow-wrap: anywhere; }
.sub { color: var(--muted); font-size: 11px; }
.line { margin: 0; font-size: 12px; color: var(--muted); line-height: 1.5; }
.line.warn { color: var(--warn); }
.muted { margin: 0; font-size: 12px; color: var(--muted); }
.up { color: var(--pos); }
.down { color: var(--neg); }
.links { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; font-size: 12px; }
.links a { color: var(--text); text-decoration: none; display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.links a:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.lm { list-style: none; margin: 0; padding: 0; display: grid; gap: 2px; font-size: 11px; font-variant-numeric: tabular-nums; }
.lm li { display: grid; grid-template-columns: 70px 1fr auto; align-items: center; gap: var(--space-2); padding: 1px 4px; border-radius: 3px; }
.lm li.cur { background: color-mix(in srgb, var(--accent) 14%, transparent); box-shadow: inset 2px 0 0 var(--accent); }
.lpx { color: var(--muted); }
.bars { position: relative; height: 8px; }
.bars::before { content: ''; position: absolute; left: 50%; top: -1px; bottom: -1px; width: 1px; background: var(--line2); }
.bars i { position: absolute; top: 0; height: 100%; border-radius: 2px; }
.bl { right: 50%; background: var(--neg); opacity: 0.8; }
.bs { left: 50%; background: var(--pos); opacity: 0.8; }
.lv { color: var(--muted); min-width: 64px; text-align: right; }
.book { display: grid; gap: 3px; }
.bk { display: block; height: 6px; border-radius: 3px; background: color-mix(in srgb, var(--neg) 55%, var(--panel2)); overflow: hidden; }
.bk i { display: block; height: 100%; background: color-mix(in srgb, var(--pos) 70%, var(--panel2)); }
.bl-t { display: flex; justify-content: space-between; font-size: 11px; }
.walls { list-style: none; margin: 0; padding: 0; display: grid; gap: 3px; font-size: 12px; font-variant-numeric: tabular-nums; }
@media (hover: hover) and (pointer: fine) {
  .x:hover { color: var(--text); background: var(--panel2); }
  .links a:hover { color: var(--accent); }
}
</style>
