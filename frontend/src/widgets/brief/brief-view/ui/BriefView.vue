<script setup lang="ts">
import { computed } from 'vue'
import { briefMs, coinStat, hotRows, scopeText, type Brief, type BriefPosition } from '@/entities/brief'
import { planShort, routeFor } from '@/entities/trading'
import { UiChip } from '@/shared/ui'
import { ageText, fixed, price, signed, useNow } from '@/shared/lib'
import BriefLevels from './BriefLevels.vue'

/**
 * Содержимое одного досье: то, на что смотрел мозг перед решением. Сверху сводка (входы разрешены или нет, запас риска, капитал, KILL), затем блоки; оглавление прыгает к блоку.
 * Показываются только блоки, которые есть в досье (событийное досье неполное). Плану, идее и монете соответствуют ссылки на их страницы. Тексты мозга только свои прошлые тезисы и чек-лист.
 */
const props = defineProps<{ brief: Brief; id: string }>()
const now = useNow()

const b = computed(() => props.brief)
const ms = computed(() => briefMs(b.value.ts))
const when = computed(() => (ms.value == null ? '' : new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(new Date(ms.value))))
const tone = (v: number | null | undefined) => (v == null || v === 0 ? '' : v > 0 ? 'up' : 'down')
const usd = (v: number | null | undefined, d = 2) => (v == null ? '–' : `${fixed(v, d)} USDT`)
const yn = (v: boolean | null | undefined) => (v === true ? 'да' : v === false ? 'нет' : '–')

const structKeys = computed(() => Object.keys(b.value.struct?.s ?? {}))
const levelKeys = computed(() => Object.keys(b.value.levels ?? {}).filter((k) => b.value.levels![k] && Object.keys(b.value.levels![k]).length > 1))
const hot = computed(() => hotRows(b.value))
const ideas = computed(() => b.value.ideas?.active ?? [])

const sections = computed(() => {
  const x = b.value
  const out: Array<{ id: string; label: string }> = [{ id: 'sum', label: 'Сводка' }]
  if (x.system) out.push({ id: 'sys', label: 'Система' })
  if (x.account || x.session || x.room) out.push({ id: 'acc', label: 'Счёт и запас' })
  if (x.positions?.length) out.push({ id: 'pos', label: 'Позиции' })
  if (x.plans?.length) out.push({ id: 'pl', label: 'Планы' })
  if (x.market) out.push({ id: 'mkt', label: 'Рынок' })
  if (structKeys.value.length) out.push({ id: 'st', label: 'Структура' })
  if (x.limits || x.risk_level || x.economics) out.push({ id: 'lim', label: 'Лимиты' })
  if (hot.value.length) out.push({ id: 'hot', label: 'Горячие' })
  if (x.intel) out.push({ id: 'mac', label: 'Макро' })
  if (levelKeys.value.length) out.push({ id: 'lv', label: 'Уровни' })
  if (ideas.value.length) out.push({ id: 'id', label: 'Идеи' })
  if (x.recent?.length) out.push({ id: 'ev', label: 'События' })
  if (x.lessons?.length || x.checklist?.length) out.push({ id: 'ctx', label: 'Уроки и чек-лист' })
  return out
})
function jump(id: string): void {
  document.getElementById(`bf-${id}`)?.scrollIntoView({ block: 'start', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' })
}

interface Tile { k: string; v: string; sub?: string; tone?: string }
const tiles = computed<Tile[]>(() => {
  const x = b.value
  const out: Tile[] = []
  if (x.room?.allowed != null) out.push({ k: 'Входы', v: x.room.allowed ? 'разрешены' : 'запрещены', sub: x.room.reasons?.length ? x.room.reasons.join('; ') : undefined, tone: x.room.allowed ? 'up' : 'down' })
  if (x.room?.next_risk_usdt != null) out.push({ k: 'Риск на сделку', v: `до ${usd(x.room.next_risk_usdt)}`, sub: x.room.limited_by ? `упирается в ${x.room.limited_by}` : undefined })
  const slots = x.room?.slots_free ?? x.limits?.slots
  if (slots != null) out.push({ k: 'Свободных слотов', v: String(slots) })
  if (x.room?.daily_room != null) out.push({ k: 'Дневной запас', v: usd(x.room.daily_room) })
  if (x.account?.equity != null) out.push({ k: 'Капитал', v: usd(x.account.equity), sub: x.account.free != null ? `свободно ${usd(x.account.free)}` : undefined })
  if (x.account?.day_pnl != null) out.push({ k: 'Результат дня', v: `${signed(x.account.day_pnl)} USDT`, tone: tone(x.account.day_pnl), sub: `сделок ${x.account.day_trades ?? '–'} · стопов ${x.account.stops_today ?? '–'}` })
  if (x.system?.kill != null) out.push({ k: 'KILL', v: x.system.kill ? 'включён' : 'выключен', tone: x.system.kill ? 'down' : '' })
  if (x.risk_level?.level != null) out.push({ k: 'Уровень риска', v: String(x.risk_level.level) })
  return out
})

function routeOf(p: BriefPosition) {
  return routeFor(p.side, p.sl, p.avg, p.px, (p.targets ?? []).map((t) => t.px))
}
const u = (v: number) => v * 100
const coinLines = computed(() => {
  const m = b.value.market
  if (!m) return []
  const s = b.value.struct?.s ?? {}
  return [['BTC', coinStat(m.btc, s.BTC)], ['ETH', coinStat(m.eth, s.ETH)]] as const
})
const nextEvents = computed(() => b.value.intel?.calendar?.next ?? [])
const eventLine = (e: { type: string; in_min: number }) => `${e.type} через ${e.in_min >= 120 ? Math.round(e.in_min / 60) + ' ч' : e.in_min + ' мин'}`
const kvLine = (d: Record<string, unknown> | undefined) => Object.entries(d ?? {}).slice(0, 6).map(([k, v]) => `${k}=${v}`).join(' · ')
</script>

<template>
  <article class="view" :aria-label="`Досье ${id}`">
    <header class="head">
      <b class="id">{{ brief.brief_id ?? id }}</b>
      <UiChip>{{ scopeText(brief.scope) }}</UiChip>
      <span class="when">{{ when }}<template v-if="ms != null"> · {{ ageText(ms / 1000, now) }}</template></span>
      <UiChip v-if="brief.trunc" tone="warn" title="Досье не поместилось в бюджет и было обрезано">обрезано по бюджету</UiChip>
    </header>

    <nav class="toc" aria-label="Блоки досье">
      <button v-for="s in sections" :key="s.id" type="button" class="toc-b" @click="jump(s.id)">{{ s.label }}</button>
    </nav>

    <section id="bf-sum" class="blk">
      <ul v-if="tiles.length" class="tiles">
        <li v-for="t in tiles" :key="t.k" class="tile">
          <span class="k">{{ t.k }}</span>
          <b class="v" :class="t.tone">{{ t.v }}</b>
          <span v-if="t.sub" class="sub">{{ t.sub }}</span>
        </li>
      </ul>
      <p v-else class="muted">В этом досье нет данных для сводки.</p>
    </section>

    <section v-if="brief.system" id="bf-sys" class="blk">
      <h3>Система</h3>
      <ul class="chips">
        <li v-if="brief.system.exec?.alive != null"><UiChip :tone="brief.system.exec.alive ? 'ok' : 'bad'">исполнитель {{ brief.system.exec.alive ? 'жив' : 'не отвечает' }}</UiChip></li>
        <li v-if="brief.system.scanner?.alive != null"><UiChip :tone="brief.system.scanner.alive ? 'ok' : 'bad'">сканер {{ brief.system.scanner.alive ? 'жив' : 'стоит' }}</UiChip></li>
        <li v-if="brief.system.feed?.alive != null"><UiChip :tone="brief.system.feed.alive ? 'ok' : 'bad'">фид {{ brief.system.feed.alive ? 'жив' : 'стоит' }}</UiChip></li>
        <li v-if="brief.system.exec?.env"><UiChip>{{ [brief.system.exec.env, brief.system.exec.mode].filter(Boolean).join(' · ') }}{{ brief.system.exec.observe ? ' · observe' : '' }}</UiChip></li>
        <li v-if="brief.system.exec?.hold"><UiChip tone="warn">hold {{ brief.system.exec.hold }}</UiChip></li>
        <li v-if="brief.system.unacked != null"><UiChip>не подтверждено событий: {{ brief.system.unacked }}</UiChip></li>
      </ul>
    </section>

    <section v-if="brief.account || brief.session || brief.room" id="bf-acc" class="blk">
      <h3>Счёт и запас риска</h3>
      <dl class="kv">
        <div v-if="brief.account?.dd_pct != null"><dt>Просадка · множитель</dt><dd>{{ fixed(brief.account.dd_pct, 1) }}% · ×{{ fixed(brief.account.dd_mult) }}</dd></div>
        <div v-if="brief.account?.stop_streak != null"><dt>Серия стопов</dt><dd>{{ brief.account.stop_streak }}</dd></div>
        <div v-if="brief.session"><dt>Сессия</dt><dd>{{ brief.session.type ?? '–' }}{{ brief.session.min_left != null ? ` · осталось ${brief.session.min_left} мин` : '' }} · бюджет {{ usd(brief.session.budget) }} · сделок {{ brief.session.trades ?? '–' }}/{{ brief.session.max_trades ?? '–' }}</dd></div>
        <div v-if="brief.session?.realized != null"><dt>Реализовано · риск открытых</dt><dd>{{ signed(brief.session.realized) }} · {{ usd(brief.session.open_risk) }}</dd></div>
        <div v-if="brief.room?.session_room != null"><dt>Запас сессии</dt><dd>{{ usd(brief.room.session_room) }}</dd></div>
        <div v-if="brief.room?.cooldowns"><dt>Паузы по монетам</dt><dd>{{ brief.room.cooldowns.length ? brief.room.cooldowns.map((c) => `${c.sym} ещё ${c.min_left} мин`).join(', ') : 'нет' }}</dd></div>
        <div v-if="brief.room?.pause_min_left"><dt>Общая пауза</dt><dd>ещё {{ brief.room.pause_min_left }} мин</dd></div>
      </dl>
    </section>

    <section v-if="brief.positions?.length" id="bf-pos" class="blk">
      <h3>Позиции</h3>
      <ul class="list">
        <li v-for="p in brief.positions" :key="p.plan ?? p.sym" class="pos">
          <div class="line">
            <router-link class="sym" :to="{ path: '/market', query: { coin: p.sym } }">{{ p.sym }}</router-link>
            <UiChip>{{ p.side === 'long' ? 'лонг' : 'шорт' }}</UiChip>
            <router-link v-if="p.plan" class="lnk" :to="{ path: '/plans', query: { plan: p.plan } }">{{ planShort(p.plan) }}</router-link>
            <b class="r" :class="tone(p.r)">{{ p.r != null ? signed(p.r) + 'R' : '–' }}</b>
          </div>
          <div v-if="routeOf(p)" class="rt">
            <svg viewBox="0 0 100 12" preserveAspectRatio="none" role="img" :aria-label="`${p.sym}: стоп ${price(p.sl)}, вход ${price(p.avg)}, цена ${price(p.px)}`">
              <rect class="trk" x="0" y="5" width="100" height="2" />
              <rect class="rsk" x="0" y="3.5" :width="u(routeOf(p)!.entry)" height="5" />
              <line v-for="(t, i) in routeOf(p)!.targets" :key="i" class="tk" :x1="u(t)" :x2="u(t)" y1="1" y2="11" />
              <line class="en" :x1="u(routeOf(p)!.entry)" :x2="u(routeOf(p)!.entry)" y1="0" y2="12" />
              <line v-if="routeOf(p)!.price != null" class="nw" :x1="u(routeOf(p)!.price!)" :x2="u(routeOf(p)!.price!)" y1="0" y2="12" />
            </svg>
          </div>
          <p class="meta">вход {{ price(p.avg) }} · цена {{ price(p.px) }} · стоп {{ price(p.sl) }} · ликвидация {{ price(p.liq) }} · MAE {{ fixed(p.mae_r) }}R · MFE {{ fixed(p.mfe_r) }}R · {{ p.age_min ?? '–' }} мин</p>
        </li>
      </ul>
    </section>

    <section v-if="brief.plans?.length" id="bf-pl" class="blk">
      <h3>Планы</h3>
      <ul class="list">
        <li v-for="p in brief.plans" :key="p.id" class="pl">
          <div class="line">
            <router-link class="sym" :to="{ path: '/market', query: { coin: p.sym } }">{{ p.sym }}</router-link>
            <UiChip>{{ p.side === 'long' ? 'лонг' : 'шорт' }}</UiChip>
            <UiChip v-if="p.status">{{ p.status }}</UiChip>
            <router-link class="lnk" :to="{ path: '/plans', query: { plan: p.id } }">{{ planShort(p.id) }}{{ p.rev != null ? ' r' + p.rev : '' }}</router-link>
            <span v-if="p.dist_pct != null" class="muted end">до уровня {{ fixed(p.dist_pct) }}%</span>
          </div>
          <p v-if="p.entry" class="meta">{{ p.entry }}{{ p.ttl_left_min != null ? ` · TTL ${p.ttl_left_min} мин` : '' }}</p>
          <p v-if="p.own_thesis" class="thesis"><span>свой тезис</span> {{ p.own_thesis }}</p>
        </li>
      </ul>
    </section>

    <section v-if="brief.market" id="bf-mkt" class="blk">
      <h3>Рынок</h3>
      <div class="coins">
        <div v-for="[name, c] in coinLines" :key="name" class="coin">
          <b>{{ name }}</b>
          <span v-if="c.px == null && c.r5 == null && c.r1h == null" class="m">нет данных в этом досье</span>
          <template v-else>
          <span class="px">{{ price(c.px) }}</span>
          <span class="m" :class="tone(c.r5)">5м {{ c.r5 != null ? signed(c.r5) + '%' : '–' }}</span>
          <span class="m" :class="tone(c.r1h)">1ч {{ c.r1h != null ? signed(c.r1h) + '%' : '–' }}</span>
          <span class="m" :class="tone(c.r24h)">24ч {{ c.r24h != null ? signed(c.r24h) + '%' : '–' }}</span>
          <span class="m">ATR 5м {{ fixed(c.atr5) }}%</span>
          <span class="m">funding {{ fixed(c.funding, 4) }}%</span>
          <span v-if="c.regime" class="m">режим {{ c.regime }}</span>
          </template>
        </div>
      </div>
      <ul class="chips">
        <li v-if="brief.market.utc_session"><UiChip>сессия {{ brief.market.utc_session }}</UiChip></li>
        <li v-if="brief.market.risk_off != null"><UiChip :tone="brief.market.risk_off ? 'bad' : 'neutral'">risk_off: {{ yn(brief.market.risk_off) }}</UiChip></li>
        <li v-if="brief.market.news_mode"><UiChip>новости: {{ brief.market.news_mode }}</UiChip></li>
        <li v-if="brief.market.blackout"><UiChip tone="warn">окно новостей: {{ brief.market.blackout.event }} через {{ brief.market.blackout.in_min }} мин</UiChip></li>
      </ul>
    </section>

    <section v-if="structKeys.length" id="bf-st" class="blk">
      <h3>Структура рынка</h3>
      <div class="scroll">
        <table class="tbl">
          <thead><tr><th>Монета</th><th>Режим</th><th>ATR 5м %</th><th>ATR 1ч %</th><th>1ч %</th><th>24ч %</th><th>Позиция в дне</th><th>Спред бп</th><th>Funding 8ч %</th></tr></thead>
          <tbody>
            <tr v-for="k in structKeys" :key="k">
              <td><router-link class="sym" :to="{ path: '/market', query: { coin: k } }">{{ k }}</router-link></td>
              <td>{{ brief.struct!.s![k].reg ?? '–' }}</td>
              <td>{{ fixed(brief.struct!.s![k].a5_pct) }}</td><td>{{ fixed(brief.struct!.s![k].a1h_pct) }}</td>
              <td :class="tone(brief.struct!.s![k].r1h)">{{ signed(brief.struct!.s![k].r1h) }}</td><td :class="tone(brief.struct!.s![k].r24h)">{{ signed(brief.struct!.s![k].r24h) }}</td>
              <td>{{ fixed(brief.struct!.s![k].dpos) }}</td><td>{{ fixed(brief.struct!.s![k].sp) }}</td><td>{{ fixed(brief.struct!.s![k].fu, 4) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="muted">ATR в % цены; позиция в дне: 0 низ, 1 верх.<template v-if="brief.struct?.stale_s"> Данные устарели на {{ brief.struct.stale_s }} с.</template></p>
    </section>

    <section v-if="brief.limits || brief.risk_level || brief.economics" id="bf-lim" class="blk">
      <h3>Лимиты и уровень риска</h3>
      <dl class="kv">
        <div v-if="brief.limits?.risk_per_trade_usdt != null"><dt>Риск на сделку</dt><dd>{{ usd(brief.limits.risk_per_trade_usdt) }}</dd></div>
        <div v-if="brief.limits?.daily_loss_usdt != null"><dt>Дневной лимит · осталось</dt><dd>{{ fixed(brief.limits.daily_loss_usdt) }} · {{ usd(brief.limits.daily_left_usdt) }}</dd></div>
        <div v-if="brief.limits?.max_open_positions != null"><dt>Позиции · новые планы</dt><dd>{{ brief.limits.max_open_positions }} · {{ brief.limits.max_new_plans ?? '–' }}</dd></div>
        <div v-if="brief.limits?.size_mult != null"><dt>Множитель размера</dt><dd>×{{ fixed(brief.limits.size_mult) }}</dd></div>
        <div v-if="brief.risk_level?.carry"><dt>Перенос позиций</dt><dd>{{ brief.risk_level.carry.policy }}, макс. {{ brief.risk_level.carry.max_positions ?? '–' }}</dd></div>
        <div v-if="brief.economics?.llm_usd_day != null"><dt>Расход LLM за сутки</dt><dd>{{ usd(brief.economics.llm_usd_day, 4) }}</dd></div>
        <div v-if="brief.economics?.trades_day != null"><dt>Сделок за день · минимум</dt><dd>{{ brief.economics.trades_day }} · {{ brief.economics.min_trades_day ?? '–' }}</dd></div>
      </dl>
    </section>

    <section v-if="hot.length" id="bf-hot" class="blk">
      <h3>Горячие монеты</h3>
      <div class="scroll">
        <table class="tbl">
          <thead><tr><th>Монета</th><th>Счёт</th><th>Причины</th><th>Цена</th><th>5м</th><th>Спред бп</th><th>Funding %</th><th>Лот</th></tr></thead>
          <tbody>
            <tr v-for="h in hot" :key="h.sym">
              <td><router-link class="sym" :to="{ path: '/market', query: { coin: h.sym } }">{{ h.sym }}</router-link><UiChip v-if="h.tradable === false" tone="warn">нельзя</UiChip></td>
              <td>{{ fixed(h.score, 1) }}</td><td class="wrap">{{ (h.reasons ?? []).join(', ') }}</td><td>{{ price(h.px) }}</td>
              <td :class="tone(h.ret_5m_pct)">{{ signed(h.ret_5m_pct, 1) }}</td><td>{{ fixed(h.spread_bps, 1) }}</td><td>{{ fixed(h.funding_pct, 3) }}</td><td>{{ fixed(h.lot_usdt, 1) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section v-if="brief.intel" id="bf-mac" class="blk">
      <h3>Макро и новости</h3>
      <dl class="kv">
        <div v-if="brief.intel.sentiment?.fng?.v != null"><dt>Fear &amp; Greed</dt><dd>{{ brief.intel.sentiment.fng.v }} ({{ brief.intel.sentiment.fng.cls ?? '–' }}, вчера {{ brief.intel.sentiment.fng.prev ?? '–' }})</dd></div>
        <div v-if="brief.intel.sentiment?.dvol"><dt>DVOL BTC · ETH</dt><dd>{{ brief.intel.sentiment.dvol.BTC ?? '–' }} · {{ brief.intel.sentiment.dvol.ETH ?? '–' }}</dd></div>
        <div v-if="brief.intel.sentiment?.dom?.v != null"><dt>Доминация BTC</dt><dd>{{ fixed(brief.intel.sentiment.dom.v, 1) }}%</dd></div>
        <div><dt>Календарь</dt><dd>{{ nextEvents.length ? nextEvents.slice(0, 3).map(eventLine).join(', ') : 'нет ближайших событий' }}</dd></div>
        <div v-if="brief.news_digest?.events"><dt>Новостей в дайджесте</dt><dd>{{ brief.news_digest.events.length }}</dd></div>
        <div v-if="brief.intel.degraded?.length"><dt>Источники с ошибкой</dt><dd>{{ brief.intel.degraded.join(', ') }}</dd></div>
      </dl>
    </section>

    <section v-if="levelKeys.length" id="bf-lv" class="blk">
      <h3>Уровни</h3>
      <BriefLevels v-for="k in levelKeys" :key="k" :sym="k" :level="brief.levels![k]" />
    </section>

    <section v-if="ideas.length" id="bf-id" class="blk">
      <h3>Идеи</h3>
      <ul class="list">
        <li v-for="i in ideas" :key="i.id" class="line">
          <router-link class="lnk" :to="{ path: '/ideas', query: { idea: i.id } }">{{ i.id }}</router-link>
          <router-link class="sym" :to="{ path: '/market', query: { coin: i.sym } }">{{ i.sym }}</router-link>
          <span class="muted">{{ i.side }} · уровень {{ price(i.level) }}{{ i.dist_pct != null ? ` (${fixed(i.dist_pct, 1)}%)` : '' }} · {{ i.status ?? '' }}{{ i.age_h != null ? ` · ${fixed(i.age_h, 1)} ч` : '' }}</span>
        </li>
      </ul>
    </section>

    <section v-if="brief.recent?.length" id="bf-ev" class="blk">
      <h3>Последние события</h3>
      <ul class="list">
        <li v-for="e in brief.recent" :key="e.seq" class="ev"><span class="muted">#{{ e.seq }}</span> {{ [e.type, e.sym, kvLine(e.d)].filter(Boolean).join(' · ') }}</li>
      </ul>
    </section>

    <section v-if="brief.lessons?.length || brief.checklist?.length" id="bf-ctx" class="blk">
      <template v-if="brief.lessons?.length"><h3>Уроки</h3><ul class="plain"><li v-for="(t, i) in brief.lessons" :key="i">{{ t }}</li></ul></template>
      <template v-if="brief.checklist?.length"><h3>Чек-лист владельца</h3><ul class="plain"><li v-for="c in brief.checklist" :key="c.id"><b class="mono">{{ c.id }}</b> {{ c.text }}</li></ul></template>
    </section>
  </article>
</template>

<style scoped>
.view { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: var(--space-4); min-width: 0; display: grid; gap: var(--space-4); align-content: start; }
.head { display: flex; align-items: center; flex-wrap: wrap; gap: var(--space-2) var(--space-3); }
.id { font-family: var(--mono, ui-monospace, monospace); font-size: 13px; }
.when { font-size: 12px; color: var(--muted); }
.toc { position: sticky; top: 0; z-index: 2; display: flex; flex-wrap: wrap; gap: var(--space-1); padding: var(--space-2) 0; margin: calc(var(--space-2) * -1) 0; background: var(--panel); }
.toc-b { padding: 3px 10px; border: 1px solid var(--line2); border-radius: 99px; background: none; color: var(--muted); font: inherit; font-size: 12px; cursor: pointer; transition: color var(--dur-fast) ease, border-color var(--dur-fast) ease; }
.toc-b:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.blk { display: grid; gap: var(--space-3); scroll-margin-top: 56px; }
.blk h3 { margin: 0; font-size: 12px; font-weight: 600; color: var(--muted); }
.tiles { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: var(--space-3); }
.tile { display: grid; gap: 2px; padding: var(--space-3); border-radius: var(--radius-sm); background: var(--panel2); min-width: 0; }
.k { font-size: 11px; color: var(--muted); }
.v { font-size: 17px; font-variant-numeric: tabular-nums; letter-spacing: -0.01em; }
.sub { font-size: 11px; color: var(--muted); overflow-wrap: anywhere; }
.up { color: var(--pos); }
.down { color: var(--neg); }
.chips { list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: var(--space-2); }
.kv { margin: 0; display: grid; gap: 5px; font-size: 12px; }
.kv > div { display: flex; justify-content: space-between; gap: var(--space-4); }
dt { color: var(--muted); }
dd { margin: 0; text-align: right; overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }
.list { list-style: none; margin: 0; padding: 0; display: grid; gap: var(--space-3); }
.line { display: flex; align-items: center; flex-wrap: wrap; gap: var(--space-2); font-size: 12px; }
.sym { font-weight: 700; color: var(--text); text-decoration: none; }
.lnk { color: var(--accent); text-decoration: none; font-size: 12px; }
.sym:focus-visible, .lnk:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.r { margin-left: auto; font-variant-numeric: tabular-nums; }
.end { margin-left: auto; }
.meta { margin: 2px 0 0; font-size: 11px; color: var(--muted); font-variant-numeric: tabular-nums; }
.muted { margin: 0; font-size: 11px; color: var(--muted); }
.rt svg { display: block; width: 100%; height: 14px; margin-top: var(--space-1); overflow: visible; }
.trk { fill: var(--line2); }
.rsk { fill: var(--neg); opacity: 0.22; }
.tk { stroke: var(--pos); stroke-width: 1.4; vector-effect: non-scaling-stroke; opacity: 0.8; }
.en { stroke: var(--accent); stroke-width: 2; vector-effect: non-scaling-stroke; }
.nw { stroke: var(--text); stroke-width: 2.4; vector-effect: non-scaling-stroke; }
.thesis { margin: 4px 0 0; font-size: 12px; color: var(--muted); line-height: 1.45; }
.thesis span { padding: 0 6px; border: 1px solid var(--line2); border-radius: 99px; font-size: 10px; margin-right: 4px; }
.coins { display: grid; gap: var(--space-2); }
.coin { display: flex; flex-wrap: wrap; align-items: baseline; gap: var(--space-1) var(--space-4); padding: var(--space-3); border-radius: var(--radius-sm); background: var(--panel2); font-size: 12px; font-variant-numeric: tabular-nums; }
.coin b { font-size: 14px; }
.coin .px { font-weight: 600; font-size: 14px; }
.m { color: var(--muted); }
.m.up { color: var(--pos); }
.m.down { color: var(--neg); }
.scroll { overflow-x: auto; }
.tbl { width: 100%; border-collapse: collapse; font-size: 12px; font-variant-numeric: tabular-nums; }
.tbl th { text-align: right; font-weight: 400; color: var(--muted); padding: 4px var(--space-2); white-space: nowrap; border-bottom: 1px solid var(--line); }
.tbl td { text-align: right; padding: 4px var(--space-2); white-space: nowrap; border-bottom: 1px solid var(--line); }
.tbl th:first-child, .tbl td:first-child { text-align: left; }
.tbl .wrap { white-space: normal; text-align: left; min-width: 140px; }
.ev { font-size: 12px; overflow-wrap: anywhere; }
.plain { margin: 0; padding-left: var(--space-4); display: grid; gap: 4px; font-size: 12px; line-height: 1.45; }
.mono { font-family: var(--mono, ui-monospace, monospace); }
@media (hover: hover) and (pointer: fine) {
  .toc-b:hover { color: var(--text); border-color: var(--muted); }
  .sym:hover, .lnk:hover { color: var(--accent); }
}
</style>
