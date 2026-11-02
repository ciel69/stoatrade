/**
 * Сценарий 1. Регрессия «кнопка не работает без F5»: обход вкладок «Торговли» и «Системы» туда-обратно, данные и кнопки живые без перезагрузки страницы.
 */
import assert from 'node:assert/strict'
import { scenario } from '../lib/harness.mjs'

/** Вкладка раздела «Торговля»: подпись и текст, по которому видно, что страница показана. */
const TRADING = [
  { label: 'Сделки', path: '/trading/trades', text: 'История сделок' },
  { label: 'Планы', path: '/trading/plans', text: 'Ждёт входа' },
  { label: 'Идеи', path: '/trading/ideas', text: 'Ждут уровня' },
  { label: 'День', path: '/trading/day', text: 'Торговый день ещё не запускался' },
  { label: 'Эксперименты', path: '/trading/experiments', text: 'Результаты по группам' },
]
const SYSTEM = [
  { label: 'Управление', path: '/system/control', text: 'Насколько смелые сделки разрешены' },
  { label: 'Настройки', path: '/system/settings', text: 'Кто думает' },
  { label: 'Уведомления', path: '/system/notify', text: 'Уведомления на компьютере' },
]

scenario('Вкладки «Торговли» и «Системы»: обход туда-обратно без перезагрузки, данные и кнопки живые', async ({ page, open, dash }) => {
  await open('/trading/trades')
  await page.evaluate(() => { window.__e2e_marker = 'не перезагружалась' })
  const main = page.locator('main').first()
  const go = async (section, tab) => {
    await page.getByRole('navigation', { name: section, exact: true }).getByRole('link', { name: new RegExp(`^${tab.label}`) }).click()
    await page.waitForURL((u) => u.pathname === tab.path)
    await main.getByText(tab.text, { exact: false }).first().waitFor()
  }

  // два круга по вкладкам «Торговли»
  for (let lap = 0; lap < 2; lap++) for (const tab of [...TRADING.slice(1), TRADING[0]]) await go('Торговля', tab)

  // данные идут и после возврата: сервер меняет планы, страница «Планы» показывает новое без обновления
  await go('Торговля', TRADING[1])
  dash.patch('plans', (p) => { p.items[0].sym = 'AVAX' })
  await main.getByText('AVAX', { exact: true }).waitFor()
  // кнопка живая: клик по строке плана открывает боковую панель и пишет выбор в адрес
  await main.locator('li.pick').filter({ hasText: 'AVAX' }).click()
  await page.waitForURL((u) => u.searchParams.get('plan') === 'PL-B1')
  await page.getByRole('dialog', { name: 'План' }).waitFor()
  await page.keyboard.press('Escape')
  await page.getByRole('dialog', { name: 'План' }).waitFor({ state: 'hidden' })

  // «Сделки»: новая сделка от сервера появляется в истории
  await go('Торговля', TRADING[0])
  dash.patch('trades', (t) => { t.items.unshift({ ...t.items[0], id: 'T-9', sym: 'XRPUSDT' }); t.total += 1 })
  await main.getByText('XRPUSDT').waitFor()

  // раздел «Система» туда-обратно через боковое меню и вкладки
  await page.getByRole('complementary', { name: 'Разделы' }).getByRole('link', { name: /^Система/ }).click()
  await page.waitForURL((u) => u.pathname === '/system/control')
  for (let lap = 0; lap < 2; lap++) for (const tab of [...SYSTEM.slice(1), SYSTEM[0]]) await go('Система', tab)

  // на «Управлении» кнопки уровня риска нажимаются и открывают панель изменений
  const levels = page.getByRole('group', { name: 'Уровень риска' })
  await levels.getByRole('button', { name: /^2\b/ }).click()
  await page.getByRole('button', { name: 'Применить уровень' }).waitFor()
  // и после ухода на другой раздел и возврата снова живые
  await page.getByRole('complementary', { name: 'Разделы' }).getByRole('link', { name: /^Обзор/ }).click()
  await page.getByText('Открытые позиции').first().waitFor()
  await page.getByRole('complementary', { name: 'Разделы' }).getByRole('link', { name: /^Система/ }).click()
  await levels.getByRole('button', { name: /^4\b/ }).click()
  await page.getByRole('button', { name: 'Применить уровень' }).waitFor()

  // кнопки «назад» и «вперёд» браузера тоже работают без перезагрузки
  await page.goBack()
  await page.waitForURL((u) => u.pathname === '/')
  await page.getByText('Открытые позиции').first().waitFor()
  await page.goForward()
  await page.waitForURL((u) => u.pathname === '/system/control')
  await levels.getByRole('button', { name: /^2\b/ }).click()
  await page.getByRole('button', { name: 'Применить уровень' }).waitFor()
  await levels.getByRole('button', { name: /^3\b/ }).click()
  await page.getByRole('button', { name: 'Применить уровень' }).waitFor({ state: 'hidden' })       // 3 — текущий уровень: черновик сброшен

  assert.equal(await page.evaluate(() => window.__e2e_marker), 'не перезагружалась', 'страница перезагрузилась (метка в window потеряна)')
  assert.equal(dash.connects, 1, 'сокет переподключался при смене вкладок')
  const documents = dash.httpRequests.filter((r) => !/\.[a-z0-9]+$/i.test(r))
  assert.equal(documents.length, 1, `документ запрашивался больше одного раза: ${documents.join(', ')}`)
})
