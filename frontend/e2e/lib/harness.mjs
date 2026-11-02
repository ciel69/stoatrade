/**
 * Каркас сценария: свой фейковый сервер и свой контекст браузера на каждый сценарий (независимость), общий запущенный Edge на весь прогон.
 * При падении снимает скриншот и пишет лог (консоль, запросы, сообщения клиента сокету) в e2e/artifacts/.
 */
import { test, after } from 'node:test'
import assert from 'node:assert/strict'
import { mkdirSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { chromium } from 'playwright-core'
import { ARTIFACTS_DIR, DIST_DIR, edgePath } from './env.mjs'
import { FakeDash } from './fake-dash.mjs'
import { HELLO_FULL, dayReport, defaultTopics, expDetail, planDetail } from './fixtures.mjs'
import { existsSync } from 'node:fs'

// hidden-вкладки и фоновые окна не должны замедлять таймеры (иначе ожидания по условию растягиваются)
const BROWSER_ARGS = ['--disable-background-timer-throttling', '--disable-renderer-backgrounding', '--disable-backgrounding-occluded-windows', '--no-first-run', '--no-default-browser-check']

let browserPromise = null
export function getBrowser() {
  if (!browserPromise) {
    if (!existsSync(path.join(DIST_DIR, 'index.html'))) throw new Error('Нет frontend/dist: сначала выполните `npm --prefix frontend run build`.')
    const exe = edgePath()
    const opts = { headless: true, args: BROWSER_ARGS }
    browserPromise = chromium.launch(exe ? { ...opts, executablePath: exe } : { ...opts, channel: 'msedge' })
  }
  return browserPromise
}
after(async () => {
  if (browserPromise) await (await browserPromise).close()
})

const slug = (s) => s.replace(/[^\p{L}\p{N}]+/gu, '_').slice(0, 60)

/**
 * Определить сценарий.
 * @param {string} name название (по-русски, из него видно, что проверяется)
 * @param {object} opts viewport, colorScheme, heartbeatMs, hello (hello.ctl), topics (поправки к данным: функция от темы), allowConsole (регулярки допустимых ошибок консоли), timeout (мс)
 * @param {(ctx: Ctx) => Promise<void>} fn
 */
export function scenario(name, opts, fn) {
  if (typeof opts === 'function') { fn = opts; opts = {} }
  // opts.todo: строка с причиной. Сценарий идёт, но его падение не валит прогон (известная ошибка приложения); когда её починят, он станет зелёным.
  test(name, { timeout: opts.timeout ?? 90_000, todo: opts.todo }, async () => {
    const topics = await defaultTopics()
    if (opts.topics) opts.topics(topics)
    const dash = await new FakeDash({ topics, hello: opts.hello ?? HELLO_FULL, heartbeatMs: opts.heartbeatMs ?? 0 }).start()
    // разовые ответы по запросу (чтение): подробности плана, разрезы экспериментов, итог дня
    dash.replies.plan = async (m) => ({ topic: 'plan_detail', data: await planDetail(m.id, dash.state.get('plans')) })
    dash.replies.exp = async (m) => ({ topic: 'exp_detail', data: await expDetail(m.by, m.mode) })
    dash.replies.day_report = async (m) => ({ topic: 'day_report_detail', data: await dayReport(m.id) })
    const browser = await getBrowser()
    const context = await browser.newContext({ viewport: opts.viewport ?? { width: 1400, height: 900 }, colorScheme: opts.colorScheme ?? 'dark', locale: 'ru-RU', serviceWorkers: 'block' })
    const page = await context.newPage()
    const log = []
    const errors = []
    const requests = []
    const allow = opts.allowConsole ?? []
    const stamp = () => new Date().toISOString().slice(11, 23)
    page.on('console', (m) => {
      log.push(`${stamp()} console.${m.type()}: ${m.text()}`)
      if (m.type() === 'error' && !allow.some((re) => re.test(m.text()))) errors.push(`console.error: ${m.text()}`)
    })
    page.on('pageerror', (e) => { log.push(`${stamp()} pageerror: ${e.message}`); errors.push(`pageerror: ${e.message}`) })
    page.on('request', (r) => { requests.push({ t: Date.now(), method: r.method(), url: r.url(), type: r.resourceType() }); log.push(`${stamp()} request ${r.method()} ${r.url()}`) })
    page.on('requestfailed', (r) => log.push(`${stamp()} requestfailed ${r.url()} ${r.failure()?.errorText}`))
    page.setDefaultTimeout(opts.actionTimeout ?? 10_000)

    const ctx = { dash, page, context, errors, requests, log, base: dash.url, open: (p = '/') => open(ctx, p) }
    try {
      await fn(ctx)
      assert.deepEqual(errors, [], `Ошибки в консоли браузера:\n${errors.join('\n')}`)
    } catch (e) {
      mkdirSync(ARTIFACTS_DIR, { recursive: true })
      const base = path.join(ARTIFACTS_DIR, slug(name))
      await page.screenshot({ path: `${base}.png`, fullPage: false }).catch(() => {})
      writeFileSync(`${base}.log`, [
        `Сценарий: ${name}`, `Ошибка: ${e && e.stack ? e.stack : e}`, '', `URL: ${page.url()}`, `Подключений к сокету: ${dash.connects}`,
        '--- сообщения клиента сокету ---', ...dash.received.map((m) => JSON.stringify(m)), '--- HTTP к статике ---', ...dash.httpRequests, '--- консоль и запросы ---', ...log,
      ].join('\n'))
      throw e
    } finally {
      await context.close().catch(() => {})
      await dash.stop().catch(() => {})
    }
  })
}

/** Открыть страницу и дождаться, что приложение получило снимки (сокет подключён, кнопка старта появилась). */
export async function open(ctx, p = '/') {
  const { page, base } = ctx
  await page.goto(base + p, { waitUntil: 'domcontentloaded' })
  await page.locator('#app > *').first().waitFor()
  await waitFor(() => ctx.dash.connects >= 1, 'сокет не подключился к фейковому серверу')
  await page.locator('aside[aria-label="Разделы"]').waitFor()
}

/** Ожидание условия по опросу (без фиксированных пауз). */
export async function waitFor(fn, message, timeout = 10_000) {
  const t0 = Date.now()
  for (;;) {
    if (await fn()) return
    if (Date.now() - t0 > timeout) throw new Error(`Не дождались: ${message}`)
    await new Promise((r) => setTimeout(r, 40))
  }
}

/**
 * Окно «Готово» не закрывается само, пока курсор над ним (hover). Наведение и уход курсора делаем явно, а не полагаемся на то, где курсор оказался после клика:
 * окно меняет размер, и без реального движения браузер событий pointerleave не шлёт.
 */
export async function leaveDialog(page, dialog) {
  await dialog.hover()
  await page.mouse.move(5, 5)
}

/** Горизонтальной прокрутки у страницы нет (с допуском 1 px на округление). */
export async function noHorizontalScroll(page) {
  return page.evaluate(() => {
    const el = document.scrollingElement || document.documentElement
    return { scrollWidth: el.scrollWidth, clientWidth: el.clientWidth, bodyScroll: document.body.scrollWidth }
  })
}
