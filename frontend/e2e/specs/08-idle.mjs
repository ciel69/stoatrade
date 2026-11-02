/**
 * Сценарий 8. Простой: критерий готовности №1 плана. После загрузки за 20 секунд нет периодических HTTP-запросов (fetch, XHR, документы, статика),
 * из сокета клиент шлёт не больше одного heartbeat/ping; пульс `link` от сервера приходит и не вызывает запросов.
 */
import assert from 'node:assert/strict'
import { scenario } from '../lib/harness.mjs'
import { T } from '../lib/env.mjs'

const PAGES = [['Обзор', '/'], ['Система: Управление', '/system/control'], ['Торговля: Планы', '/trading/plans']]

for (const [title, path] of PAGES) {
  scenario(`Простой 20 с (${title}): нет периодических HTTP-запросов, только пульс сокета`, { heartbeatMs: 4000, timeout: 90_000 }, async ({ page, open, dash, requests }) => {
    const sent = []
    page.on('websocket', (ws) => ws.on('framesent', (f) => sent.push(String(f.payload))))
    await open(path)
    await page.waitForLoadState('networkidle')                      // загрузка статики закончена
    await page.waitForFunction(() => document.querySelector('main')?.innerText.trim().length > 20)

    const from = { requests: requests.length, frames: sent.length, connects: dash.connects, pulses: dash.pulses }
    await page.waitForTimeout(T.idleWindow)                          // единственная намеренная пауза: измеряем тишину за фиксированное окно

    const extra = requests.slice(from.requests).map((r) => `${r.method} ${r.type} ${r.url}`)
    assert.deepEqual(extra, [], `в простое ушли HTTP-запросы:\n${extra.join('\n')}`)
    const frames = sent.slice(from.frames)
    assert.ok(frames.length <= 1, `клиент шлёт сокету лишнее в простое (${frames.length}): ${frames.join(' | ')}`)
    assert.equal(dash.connects, from.connects, 'сокет не должен переподключаться в простое')
    assert.ok(dash.pulses - from.pulses >= 3, `сервер должен был прислать пульс несколько раз (прислал ${dash.pulses - from.pulses})`)

    // после простоя страница жива: изменение темы доходит до экрана
    if (path === '/trading/plans') {
      dash.patch('plans', (p) => { p.items[0].sym = 'IDLE' })
      await page.locator('main').getByText('IDLE', { exact: true }).waitFor()
    }
  })
}
