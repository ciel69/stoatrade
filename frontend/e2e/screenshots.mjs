/**
 * Скриншоты нового дашборда для README и docs: `npm --prefix frontend/e2e run screenshots` (или `npm --prefix frontend run screenshots`).
 * Данные демо (lib/demo.mjs): фейковый сервер dash2 на свободном порту 127.0.0.1, реальная платформа, state/ и ключи не используются.
 * Нужен собранный frontend/dist и установленный Microsoft Edge (как у e2e; другой Chromium: E2E_EDGE_PATH=путь).
 * Результат: docs/images/<имя>-dark.png и <имя>-light.png, 1440x900 (тёмная тема основная). Фильтр по имени: `... screenshots overview`.
 */
import { existsSync, mkdirSync, statSync } from 'node:fs'
import path from 'node:path'
import { chromium } from 'playwright-core'
import { DIST_DIR, FRONTEND_DIR, edgePath } from './lib/env.mjs'
import { FakeDash } from './lib/fake-dash.mjs'
import { HELLO_FULL, dayReport, expDetail, planDetail } from './lib/fixtures.mjs'
import { demoBrief, demoTopics, demoUsage } from './lib/demo.mjs'

const OUT_DIR = path.resolve(FRONTEND_DIR, '..', 'docs', 'images')
const VIEWPORT = { width: 1440, height: 900 }

/**
 * [имя, путь, текст, по которому страница считается готовой, вид]. Каждый экран снимается в двух темах: <имя>-dark.png и <имя>-light.png.
 * Вид 'full' (по умолчанию): окно растягивается на всю высоту страницы, чтобы ни один блок не ушёл за нижний край; 'window': ровно 1440x900 (открытая панель событий).
 */
const SCREENS = [
  ['events', '/?events', 'События', 'window'],
  ['overview', '/', 'Открытые позиции'],
  ['trades', '/trading/trades', 'История сделок'],
  ['plans', '/trading/plans', 'Ждёт входа'],
  ['day', '/trading/day', 'D-20261102-1'],
  ['experiments', '/trading/experiments', 'Результаты по группам'],
  ['brain-decisions', '/brain/decisions', 'Лента решений'],
  ['brief', '/brain/brief', 'B-20261102-1640-01'],
  ['market', '/market', 'Изменение цены за 15 минут'],
  ['control', '/system/control', 'Насколько смелые сделки разрешены'],
  ['settings', '/system/settings', 'Кто думает'],
]
const THEMES = ['dark', 'light']
const SHOTS = SCREENS.flatMap(([name, route, ready, view = 'full']) => THEMES.map((theme) => [`${name}-${theme}`, route, ready, theme, view]))
/** Только для съёмки: запас снизу страницы, чтобы плавающая кнопка событий (position: fixed, правый нижний угол) не закрывала последний блок. */
const SHOT_CSS = 'main.page { padding-bottom: 88px !important; }'

async function main() {
  if (!existsSync(path.join(DIST_DIR, 'index.html'))) throw new Error('Нет frontend/dist: сначала `npm --prefix frontend run build`.')
  const filter = process.argv[2]
  const shots = SHOTS.filter(([name]) => !filter || name.includes(filter))
  mkdirSync(OUT_DIR, { recursive: true })
  const exe = edgePath()
  const opts = { headless: true, args: ['--no-first-run', '--no-default-browser-check', '--hide-scrollbars'] }
  const browser = await chromium.launch(exe ? { ...opts, executablePath: exe } : { ...opts, channel: 'msedge' })
  const dash = await new FakeDash({ topics: await demoTopics(), hello: HELLO_FULL, heartbeatMs: 5000 }).start()
  dash.replies.plan = async (m) => ({ topic: 'plan_detail', data: await planDetail(m.id, dash.state.get('plans')) })
  dash.replies.exp = async (m) => ({ topic: 'exp_detail', data: await expDetail(m.by, m.mode) })
  dash.replies.day_report = async (m) => ({ topic: 'day_report_detail', data: await dayReport(m.id) })
  dash.replies.brief = async (m) => ({ topic: 'brief_detail', data: demoBrief(m.id) })
  dash.replies.usage = async (m) => ({ topic: 'usage_detail', data: demoUsage(m.days, m.by) })
  const errors = []
  try {
    for (const [name, route, ready, theme, view] of shots) {
      // тема как у самого приложения: ключ tb.theme в localStorage, theme-init.js ставит data-theme до первого рендера (src/shared/lib/theme); медиа-запрос не эмулируется
      const context = await browser.newContext({ viewport: VIEWPORT, deviceScaleFactor: 1, locale: 'ru-RU', serviceWorkers: 'block' })
      await context.addInitScript((t) => { try { localStorage.setItem('tb.theme', t) } catch { /* без хранилища тема по системе */ } }, theme)
      const page = await context.newPage()
      page.on('pageerror', (e) => errors.push(`${name}: ${e.message}`))
      page.on('console', (m) => { if (m.type() === 'error') errors.push(`${name}: ${m.text()}`) })
      await page.goto(dash.url + route, { waitUntil: 'domcontentloaded' })
      await page.locator('aside[aria-label="Разделы"]').waitFor({ timeout: 15_000 })
      await page.getByText(ready).first().waitFor({ timeout: 15_000 })
      const applied = await page.evaluate(() => document.documentElement.getAttribute('data-theme'))
      if (applied !== theme) throw new Error(`${name}: тема не применилась (data-theme=${applied})`)
      await page.mouse.move(VIEWPORT.width - 2, VIEWPORT.height - 2)
      await page.evaluate(() => document.fonts?.ready)
      await page.waitForTimeout(900) // анимации появления и графики
      if (view === 'full') {
        await page.addStyleTag({ content: SHOT_CSS })
        const h = await page.evaluate(() => Math.max(document.documentElement.scrollHeight, document.body.scrollHeight))
        await page.setViewportSize({ width: VIEWPORT.width, height: Math.max(VIEWPORT.height, Math.ceil(h)) })
        await page.waitForTimeout(400) // перерасчёт графиков под новую высоту
      }
      const file = path.join(OUT_DIR, `${name}.png`)
      await page.screenshot({ path: file, fullPage: false })
      console.log(`${path.relative(process.cwd(), file)}  ${Math.round(statSync(file).size / 1024)} КБ`)
      await context.close()
    }
  } finally {
    await dash.stop().catch(() => {})
    await browser.close()
  }
  if (errors.length) {
    console.error(`Ошибки в консоли браузера:\n${errors.join('\n')}`)
    process.exit(1)
  }
}

main().catch((e) => { console.error(e); process.exit(1) })
