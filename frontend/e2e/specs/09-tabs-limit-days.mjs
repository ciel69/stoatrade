/**
 * Сценарий 9. Две правки по живому просмотру:
 * а) сервер отказал по числу вкладок (503): индикатор говорит «Открыто слишком много вкладок дашборда», а не «Нет связи с платформой»; клиент спросил сервер ровно один раз и переподключился сам;
 * б) страница «День»: в списке дней последние 8 и «Показать все (N)»; выбранный в адресе старый день остаётся на виду.
 */
import assert from 'node:assert/strict'
import { scenario, waitFor } from '../lib/harness.mjs'
import { T } from '../lib/env.mjs'
import { makeRunning } from '../lib/fixtures.mjs'

const CONSOLE_OK = [/WebSocket connection .* failed/i, /ERR_CONNECTION/i, /503/]
const TITLE = 'Открыто слишком много вкладок дашборда'

scenario('Отказ по числу вкладок: человеческий текст вместо «Нет связи», один вопрос серверу, возврат сам', { allowConsole: CONSOLE_OK, topics: makeRunning }, async ({ page, open, dash }) => {
  await open('/')
  const indicator = page.getByRole('button', { name: /Состояние платформы/ })
  await indicator.getByText('Всё в порядке').waitFor()

  dash.reportedClients = 12      // все слоты заняты
  dash.blockConnections = true
  dash.dropAll()
  await indicator.getByText(TITLE).waitFor()
  assert.equal(await indicator.getByText('Нет связи с платформой').count(), 0, 'при отказе по вкладкам не должно быть общего «Нет связи»')
  await indicator.click()
  await page.getByText('Закройте лишние, страница подключится сама.').first().waitFor()
  await page.keyboard.press('Escape')

  // клиент продолжает пробовать с нарастающей паузой, но сервер спрашивает один раз, не опросом
  await new Promise((r) => setTimeout(r, 4500))
  assert.equal(dash.linkRequests, 1, `GET /api/link должен быть один за обрыв, а не ${dash.linkRequests}`)

  // вкладки закрыли: место появилось, страница подключается сама
  dash.reportedClients = undefined
  dash.blockConnections = false
  const before = dash.connects
  await waitFor(() => dash.connects > before, 'клиент не переподключился после освобождения места', T.reconnect)
  await indicator.getByText('Всё в порядке').waitFor()
})

scenario('Обычный обрыв сети по-прежнему «Нет связи с платформой» (сервер говорит, что места есть)', { allowConsole: CONSOLE_OK, topics: makeRunning }, async ({ page, open, dash }) => {
  await open('/')
  const indicator = page.getByRole('button', { name: /Состояние платформы/ })
  await indicator.getByText('Всё в порядке').waitFor()
  dash.blockConnections = true
  dash.dropAll()
  await indicator.getByText('Нет связи с платформой').waitFor()
  assert.equal(await indicator.getByText(TITLE).count(), 0)
})

const days = Array.from({ length: 30 }, (_, i) => {
  const d = String(30 - i).padStart(2, '0')
  return {
    day_id: `D-202610${d}-1`, state: 'stopped', mode: 'paper', agent: 'brain', started: `2026-10-${d}T06:00:00Z`, stopped: `2026-10-${d}T21:00:00Z`, stop_source: 'auto', verdict: 'ok', net: 0.1 * (i + 1),
    carried_n: 0, questions_n: 0, acked: true, report_ready: true, needs_resume: false, abort: null,
  }
})
const withDays = (t) => { t.day = { banner: null, current: null, last: null, days } }

scenario('«День»: последние 8 дней и «Показать все (N)»', { topics: withDays }, async ({ page, open }) => {
  await open('/trading/day')
  const list = page.locator('section[aria-labelledby="dl-h"]')
  const rows = list.locator('ul.rows > li')
  await rows.first().waitFor()
  assert.equal(await rows.count(), 8, 'по умолчанию 8 последних дней')
  await list.getByRole('button', { name: 'Показать все (30)' }).click()
  assert.equal(await rows.count(), 30)
  await list.getByRole('button', { name: /Показать только последние 8/ }).click()
  assert.equal(await rows.count(), 8)
})

scenario('«День»: выбранный в адресе старый день остаётся виден', { topics: withDays }, async ({ page, open }) => {
  await open('/trading/day?day=D-20261001-1')      // самый старый из 30
  const list = page.locator('section[aria-labelledby="dl-h"]')
  const rows = list.locator('ul.rows > li')
  await rows.first().waitFor()
  assert.equal(await rows.count(), 9, '8 последних и выбранный')
  assert.equal(await list.locator('[aria-pressed="true"]').count(), 1, 'выбранный день подсвечен')
  assert.equal(await list.getByRole('button', { name: 'Показать все (30)' }).count(), 1)
})
