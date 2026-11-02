/**
 * Сценарий 2. Обрыв сокета: интерфейс показывает «нет связи», переподключается сам, после восстановления приходят снимки тем и данные обновляются без F5.
 */
import assert from 'node:assert/strict'
import { scenario, waitFor } from '../lib/harness.mjs'
import { T } from '../lib/env.mjs'
import { makeRunning } from '../lib/fixtures.mjs'

// пока сервер недоступен, Edge сам пишет в консоль об отказе сокета: это ожидаемо, остальные ошибки нет
const CONSOLE_OK = [/WebSocket connection .* failed/i, /ERR_CONNECTION/i, /503/]

scenario('Обрыв сокета: «нет связи», самостоятельное переподключение и свежие данные без F5', { allowConsole: CONSOLE_OK, topics: makeRunning }, async ({ page, open, dash }) => {
  await open('/trading/plans')
  await page.evaluate(() => { window.__e2e_marker = 'не перезагружалась' })
  const main = page.locator('main').first()
  const indicator = page.getByRole('button', { name: /Состояние платформы/ })
  const dot = indicator.locator('.dot')
  await main.getByText('SOL', { exact: true }).waitFor()
  await indicator.getByText('Всё в порядке').waitFor()
  assert.match(await dot.getAttribute('class'), /s-ok/, 'до обрыва индикатор должен быть зелёным')
  assert.equal(dash.connects, 1)

  // сервер пропал: соединения рвутся, новые не принимаются
  dash.blockConnections = true
  dash.dropAll()
  await indicator.getByText('Нет связи с платформой').waitFor()
  assert.doesNotMatch(await dot.getAttribute('class'), /s-ok/, 'без связи индикатор не может быть зелёным')
  // пока связи нет, данные на сервере меняются: страница узнает о них только после восстановления
  dash.patch('plans', (p) => { p.items[0].sym = 'AVAX' })
  assert.equal(await main.getByText('AVAX', { exact: true }).count(), 0, 'без связи данные обновляться не могут')
  const before = dash.connects

  // сервер вернулся: клиент сам переподключается с нарастающей паузой (1, 2, 4 с ...)
  dash.blockConnections = false
  await waitFor(() => dash.connects > before, 'клиент не переподключился', T.reconnect)
  await main.getByText('AVAX', { exact: true }).waitFor()                   // снимок темы пришёл заново
  await indicator.getByText('Всё в порядке').waitFor()
  assert.match(await dot.getAttribute('class'), /s-ok/, 'после восстановления индикатор снова зелёный')

  // и дальше данные идут живыми изменениями, без F5
  dash.patch('plans', (p) => { p.items[0].sym = 'LINK' })
  await main.getByText('LINK', { exact: true }).waitFor()
  assert.equal(await page.evaluate(() => window.__e2e_marker), 'не перезагружалась', 'страница перезагрузилась')
})

scenario('Обрыв сокета: повторные обрывы подряд, интерфейс каждый раз возвращается', { allowConsole: CONSOLE_OK, topics: makeRunning }, async ({ page, open, dash }) => {
  await open('/')
  const indicator = page.getByRole('button', { name: /Состояние платформы/ })
  for (let i = 1; i <= 2; i++) {
    const before = dash.connects
    dash.dropAll()
    await indicator.getByText('Нет связи с платформой').waitFor()
    await waitFor(() => dash.connects > before, `переподключение №${i}`, T.reconnect)
    await indicator.getByText('Нет связи с платформой').waitFor({ state: 'hidden' })
  }
})
