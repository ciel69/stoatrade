/**
 * Сценарий 7. Тема и адаптив: переключатель темы меняет data-theme и сохраняется после перезагрузки; на ширине 390 px у страниц нет горизонтальной прокрутки.
 */
import assert from 'node:assert/strict'
import { scenario } from '../lib/harness.mjs'

const THEME_KEY = 'tb.theme'
const html = (page) => page.evaluate(() => document.documentElement.getAttribute('data-theme'))
const bg = (page) => page.evaluate(() => getComputedStyle(document.body).backgroundColor)
const stored = (page) => page.evaluate((k) => localStorage.getItem(k), THEME_KEY)
const toggle = (page, label) => page.getByRole('group', { name: 'Тема оформления' }).getByRole('button', { name: label, exact: true })
const lightness = (rgb) => { const [r, g, b] = rgb.match(/\d+(\.\d+)?/g).map(Number); return (r + g + b) / 3 }

scenario('Тема: светлая и тёмная меняют data-theme и цвет фона, выбор переживает перезагрузку', async ({ page, open }) => {
  await open('/')
  assert.equal(await html(page), null, 'по умолчанию «как в системе»: атрибута data-theme нет')
  assert.equal(await toggle(page, 'Как в системе').getAttribute('aria-pressed'), 'true')
  const darkBg = await bg(page)
  assert.ok(lightness(darkBg) < 80, `в системной тёмной теме фон тёмный, а не ${darkBg}`)

  await toggle(page, 'Светлая').click()
  assert.equal(await html(page), 'light')
  assert.ok(lightness(await bg(page)) > 200, 'в светлой теме фон светлый')
  assert.equal(await stored(page), 'light')

  await page.reload()
  await page.locator('aside[aria-label="Разделы"]').waitFor()
  assert.equal(await html(page), 'light', 'после перезагрузки выбор сохранился')
  assert.equal(await toggle(page, 'Светлая').getAttribute('aria-pressed'), 'true')
  assert.ok(lightness(await bg(page)) > 200)

  await toggle(page, 'Тёмная').click()
  assert.equal(await html(page), 'dark')
  assert.ok(lightness(await bg(page)) < 80)
  assert.equal(await stored(page), 'dark')
  await page.reload()
  await page.locator('aside[aria-label="Разделы"]').waitFor()
  assert.equal(await html(page), 'dark', 'тёмная тема тоже сохраняется')
})

scenario('Тема «Как в системе»: атрибут снимается, цвета следуют настройке системы, запись в хранилище удаляется', async ({ page, open }) => {
  await open('/')
  await toggle(page, 'Светлая').click()
  assert.equal(await html(page), 'light')
  await toggle(page, 'Как в системе').click()
  assert.equal(await html(page), null, 'у «как в системе» атрибута data-theme нет')
  assert.equal(await stored(page), null, 'выбор «как в системе» это отсутствие записи')
  await page.emulateMedia({ colorScheme: 'light' })
  assert.ok(lightness(await bg(page)) > 200, 'система светлая: интерфейс светлый')
  await page.emulateMedia({ colorScheme: 'dark' })
  assert.ok(lightness(await bg(page)) < 80, 'система тёмная: интерфейс тёмный')
  await page.reload()
  await page.locator('aside[aria-label="Разделы"]').waitFor()
  assert.equal(await html(page), null)
})

scenario('Тема: светлая тема применяется без мигания, ещё до отрисовки приложения (theme-init)', { colorScheme: 'dark' }, async ({ page, base, context }) => {
  await context.addInitScript((k) => { try { localStorage.setItem(k, 'light') } catch { /* нет хранилища */ } }, THEME_KEY)
  await page.goto(base + '/', { waitUntil: 'commit' })
  await page.waitForFunction(() => document.documentElement.hasAttribute('data-theme'), null, { polling: 'raf' })
  assert.equal(await html(page), 'light')
})

// ---- адаптив: 390 px, без горизонтальной прокрутки
const PAGES = [
  ['Обзор', '/'], ['Рынок', '/market'], ['Новости', '/market/news'], ['Сделки', '/trading/trades'], ['Планы', '/trading/plans'], ['Идеи', '/trading/ideas'], ['День', '/trading/day'],
  ['Эксперименты', '/trading/experiments'], ['Решения', '/brain/decisions'], ['Досье', '/brain/brief'], ['Управление', '/system/control'], ['Настройки', '/system/settings'], ['Уведомления', '/system/notify'],
]

for (const theme of ['dark', 'light']) {
  for (const [title, path] of PAGES) {
    scenario(`Адаптив 390 px (${theme === 'dark' ? 'тёмная' : 'светлая'} тема): «${title}» без горизонтальной прокрутки`, { viewport: { width: 390, height: 844 }, colorScheme: theme }, async ({ page, base, dash }) => {
      await page.goto(base + path, { waitUntil: 'domcontentloaded' })
      await page.locator('main').first().waitFor()
      await page.waitForFunction(() => document.querySelector('main')?.innerText.trim().length > 20)
      // анимации входа страницы кончились (бесконечные, как спиннеры, не ждём)
      await page.evaluate(() => Promise.all(document.getAnimations().filter((a) => a.effect?.getComputedTiming().iterations !== Infinity).map((a) => a.finished.catch(() => {}))))
      const m = await page.evaluate(() => {
        const el = document.scrollingElement
        const vw = el.clientWidth
        const wide = [...document.querySelectorAll('body *')].filter((n) => { const r = n.getBoundingClientRect(); return r.width > 0 && r.right > vw + 1 && getComputedStyle(n).position !== 'fixed' })
        return { scrollWidth: el.scrollWidth, clientWidth: vw, offenders: wide.slice(0, 6).map((n) => `${n.tagName.toLowerCase()}.${String(n.className).split(' ')[0]} right=${Math.round(n.getBoundingClientRect().right)}`) }
      })
      assert.ok(m.scrollWidth <= m.clientWidth, `горизонтальная прокрутка: ширина содержимого ${m.scrollWidth} px при окне ${m.clientWidth} px. Выходят за край: ${m.offenders.join('; ')}`)
      assert.ok(dash.connects >= 1)
    })
  }
}
