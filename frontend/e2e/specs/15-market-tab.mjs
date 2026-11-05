/**
 * Сценарий 15. Вкладка «Рынок»: карта и сводный стакан (R1.7, задача 29; Requirement 19.3).
 * Фейковый сервер отдаёт тему `cmkt` и ответ `cmkt_sym_detail` по запросу `cmkt_sym`. Проверки: переключатель видов,
 * выбор монеты, лестница крупных уровней, глубина по полосам, факты и коды бирж, устаревший снимок без чисел,
 * прямая ссылка ?cm=, 390 px без горизонтальной прокрутки.
 */
import assert from 'node:assert/strict'
import { scenario, waitFor } from '../lib/harness.mjs'

const now = () => Date.now() / 1000

const snapshot = (over = {}) => ({
  ok: true,
  state: 'ok',
  ts: now(),
  age_s: 0.2,
  n_ex: { perp: 5, spot: 5 },
  n_syms: 2,
  skew_ms: 120,
  warn: [],
  venues: [],
  coins: [
    { sym: 'BTC', tier: 'deep', focus_s: 4000, cov: 0.94, mid: 65000, flags: [], n_ex: 10 },
    { sym: 'SOL', tier: 'light', focus_s: 800, cov: 0.71, mid: 150, flags: ['warmup'], n_ex: 4 },
  ],
  ...over,
})

const stale = () => ({ ok: false, state: 'stale', ts: null, age_s: null, n_ex: { perp: 0, spot: 0 }, n_syms: 0, skew_ms: null, warn: ['STALE'], venues: [], coins: [] })

const ladder = (sym) => ({
  sym,
  ts: now(),
  kinds: {
    perp: {
      mid: 65000,
      best_bid: 64990,
      best_ask: 65010,
      spread_pct: 0.031,
      source: 'bk',
      n_mkts: 4,
      levels: {
        ask: [{ px: 65050, pct: 0.077, musd: 12.5, cum: 12.5, mkts: ['binance_perp', 'okx_perp'] }, { px: 65163, pct: 0.25, musd: 3.1, cum: 15.6, mkts: ['bybit_perp'] }],
        bid: [{ px: 64947, pct: 0.082, musd: 9.1, cum: 9.1, mkts: ['bybit_perp'] }, { px: 64837, pct: 0.25, musd: 2.6, cum: 11.7, mkts: ['okx_perp'] }],
      },
      walls: {
        ask: [{ px: 65100, pct: 0.154, musd: 3.4, cum: 3.4, mkts: ['binance_perp', 'okx_perp'] }, { px: 65200, pct: 0.308, musd: 1.2, cum: 4.6, mkts: ['bybit_perp'] }],
        bid: [{ px: 64900, pct: 0.154, musd: 4.2, cum: 4.2, mkts: ['binance_perp'] }],
      },
      markets: { binance_perp: { code: 'OK', lag_ms: 120, mid: 65000 }, bybit_perp: { code: 'LAG', lag_ms: 1450, mid: 65001 } },
    },
  },
})

const detail = (sym) => ({
  ok: true,
  sym,
  ts: now(),
  age_s: 0.2,
  n_ex: 10,
  cov: 0.94,
  warn: [],
  oi: { musd: 11140, chg_5m_pct: 0.123 },
  funding: { w_pct: 0.00123, prem_ps: 1.4 },
  cvd: { '5m': { all: { pct: 10, musd: 3.4 } } },
  liq: { '5m': [1.2, 0.8] },
  venues: { binance_perp: { code: 'OK', lag_ms: 120 }, bybit_perp: { code: 'LAG', lag_ms: 1450 } },
  ladder: ladder(sym),
})

const serve = (dash) => { dash.replies.cmkt_sym = async (m) => ({ topic: 'cmkt_sym_detail', data: detail(m.sym) }) }

async function noOverflow(page, what) {
  const m = await page.evaluate(() => {
    const el = document.scrollingElement
    const vw = el.clientWidth
    const wide = [...document.querySelectorAll('main *')].filter((n) => { const r = n.getBoundingClientRect(); return r.width > 0 && r.right > vw + 1 && getComputedStyle(n).position !== 'fixed' })
    return { scrollWidth: el.scrollWidth, clientWidth: vw, offenders: wide.slice(0, 6).map((n) => `${n.tagName.toLowerCase()}.${String(n.className).split(' ')[0]} right=${Math.round(n.getBoundingClientRect().right)}`) }
  })
  assert.ok(m.scrollWidth <= m.clientWidth, `${what}: горизонтальная прокрутка ${m.scrollWidth} px при окне ${m.clientWidth} px. Выходят за край: ${m.offenders.join('; ')}`)
}

scenario('Рынок: переключатель «Карта монет» / «Сводный стакан», выбор монеты показывает лестницу и глубину', {
  topics: (t) => { t.cmkt = snapshot() },
}, async ({ page, open, dash }) => {
  serve(dash)
  await open('/market')
  const group = page.getByRole('group', { name: 'Вид рынка' })
  await group.getByRole('button', { name: 'Сводный стакан', exact: true }).click()
  await page.waitForURL(/view=cm/)
  await page.getByRole('heading', { name: 'Сводный стакан', level: 2 }).waitFor()
  dash.set('cmkt', snapshot())

  const picker = page.getByRole('combobox', { name: 'Монета' })
  await picker.waitFor()
  await picker.selectOption('BTC')
  await page.waitForURL(/cm=BTC/)
  await waitFor(() => dash.ops('cmkt_sym').some((m) => m.sym === 'BTC'), 'запрос подробностей BTC')

  const widget = page.locator('section[aria-label="Сводный рынок"]')
  const book = page.locator('section[aria-label="Сводный стакан"]')
  await book.getByRole('heading', { name: 'Стакан' }).waitFor()
  await book.getByText('3.4M').waitFor()
  const t = await widget.innerText()
  assert.match(t, /3\.4M/, 'объём уровня аска')
  assert.match(t, /4\.2M/, 'объём уровня бида')
  assert.match(t, /2 биржи/, 'источники уровня')
  assert.match(t, /середина/)
  assert.match(t, /0\.031%/, 'спред')
  assert.match(t, /Открытый интерес/)
  assert.match(t, /11\.14B/)
  assert.ok(!/binance_perp|\bLAG\b/.test(t), 'сырых кодов в тексте нет')

  await widget.getByText(/Биржи по монете/).click()
  const afterVenues = await widget.innerText()
  assert.match(afterVenues, /Binance · перп/)
  assert.match(afterVenues, /отстаёт/)

  await group.getByRole('button', { name: 'Карта монет', exact: true }).click()
  await page.waitForURL(/\/market$/)
  await page.locator('section[aria-label="Тепловая карта"]').waitFor()
  assert.ok(!(await page.getByRole('heading', { name: 'Сводный стакан', level: 2 }).count()), 'сводный стакан скрыт')
})

scenario('Рынок: прямая ссылка ?cm=SOL открывает сводный стакан с выбранной монетой', {
  topics: (t) => { t.cmkt = snapshot() },
}, async ({ page, open, dash }) => {
  serve(dash)
  await open('/market?cm=SOL')
  dash.set('cmkt', snapshot())
  await page.getByRole('heading', { name: 'Сводный стакан', level: 2 }).waitFor()
  await waitFor(() => dash.ops('cmkt_sym').some((m) => m.sym === 'SOL'), 'запрос подробностей SOL')
  const picker = page.getByRole('combobox', { name: 'Монета' })
  assert.equal(await picker.inputValue(), 'SOL')
  await page.locator('section[aria-label="Сводный стакан"]').getByRole('heading', { name: 'Стакан' }).waitFor()
})

scenario('Рынок: устаревший снимок называет причину и скрывает числа', {
  topics: (t) => { t.cmkt = snapshot() },
}, async ({ page, open, dash }) => {
  serve(dash)
  await open('/market?cm=BTC')
  dash.set('cmkt', snapshot())
  await page.getByRole('combobox', { name: 'Монета' }).waitFor()
  dash.set('cmkt', stale())
  await page.getByText('Снимок сводного рынка устарел').waitFor()
  const text = await page.locator('main').innerText()
  assert.equal(await page.getByRole('combobox', { name: 'Монета' }).count(), 0, 'выбор монеты скрыт')
  assert.ok(!/5 перп · 5 спот/.test(text), 'числа снимка скрыты')
  assert.ok(!/3\.4M/.test(text), 'стакан скрыт')
})
scenario('Рынок: 390 px без горизонтальной прокрутки на сводном стакане', {
  viewport: { width: 390, height: 844 },
  topics: (t) => { t.cmkt = snapshot() },
}, async ({ page, open, dash }) => {
  serve(dash)
  await open('/market?cm=BTC')
  dash.set('cmkt', snapshot())
  await page.locator('section[aria-label="Сводный стакан"]').getByRole('heading', { name: 'Стакан' }).waitFor()
  await noOverflow(page, '390 px, сводный стакан')
})
