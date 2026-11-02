/**
 * Смоук: все страницы открываются прямой ссылкой, получают данные от фейкового сервера и не пишут ошибок в консоль (проверка идёт в конце каждого сценария).
 * Заодно: старые адреса (/trades, /system и т.п.) ведут на новые с теми же параметрами.
 */
import assert from 'node:assert/strict'
import { scenario } from '../lib/harness.mjs'

const ROUTES = [
  ['/', 'Открытые позиции'], ['/market', 'Изменение цены за 15 минут'], ['/market/news', 'Образец: биржа объявила о листинге'], ['/trading/trades', 'История сделок'],
  ['/trading/plans', 'Ждёт входа'], ['/trading/ideas', 'Ждут уровня'], ['/trading/day', 'Торговый день ещё не запускался'], ['/trading/experiments', 'Результаты по группам'],
  ['/brain/decisions', 'Лента решений'], ['/brain/brief', 'Досье'], ['/system/control', 'Насколько смелые сделки разрешены'], ['/system/settings', 'Кто думает'], ['/system/notify', 'Уведомления на компьютере'],
]

scenario('Смоук: каждая страница открывается прямой ссылкой, показывает данные и не пишет ошибок в консоль', async ({ page, open, base }) => {
  await open('/')
  for (const [path, text] of ROUTES) {
    await page.goto(base + path)
    await page.locator('main').getByText(text).first().waitFor()
  }
})

scenario('Смоук: старые адреса перенаправляются на новые вместе с параметрами', async ({ page, open, base }) => {
  await open('/')
  const MOVED = [['/trades?trade=T-1', '/trading/trades'], ['/plans?plan=PL-B1', '/trading/plans'], ['/system', '/system/control'], ['/brief', '/brain/brief'], ['/decisions?decision=1', '/brain/decisions']]
  for (const [from, to] of MOVED) {
    await page.goto(base + from)
    const u = new URL(page.url())
    assert.equal(u.pathname, to, `${from} должен вести на ${to}`)
    assert.equal(u.search, new URL(base + from).search, 'параметры сохраняются')
  }
})
