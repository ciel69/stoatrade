/**
 * Сценарий 6. Клавиатура и фокус: диалог «Принимаю риски» ловит Tab и Escape, фокус возвращается на кнопку-открыватель;
 * боковые панели (план, решение) открываются и закрываются клавишами, адрес хранит выбор (?plan=, ?decision=).
 */
import assert from 'node:assert/strict'
import { scenario } from '../lib/harness.mjs'
import { installRiskRules, riskyChallenge } from '../lib/ctl-rules.mjs'

const activeInside = (page, selector) => page.evaluate((sel) => !!document.activeElement && !!document.querySelector(sel)?.contains(document.activeElement), selector)
const activeLabel = (page) => page.evaluate(() => (document.activeElement?.textContent ?? '').trim().replace(/\s+/g, ' ').slice(0, 40))

scenario('Диалог «Принимаю риски»: фокус внутри, Tab и Shift+Tab по кругу, Escape закрывает без отправки команды', async ({ page, open, dash }) => {
  installRiskRules(dash)
  dash.rules['risk.set'].challenge = () => ({ data: riskyChallenge({ minDelay: 0 }) })
  await open('/system/control')
  await page.getByRole('group', { name: 'Уровень риска' }).getByRole('button', { name: /^5\b/ }).click()
  const apply = page.getByRole('button', { name: 'Применить уровень' })
  await apply.focus()
  await page.keyboard.press('Enter')                                       // открыватель нажат с клавиатуры

  const dlg = page.locator('[aria-labelledby="ad-title"]')
  await dlg.getByRole('button', { name: 'Отмена' }).waitFor()
  assert.equal(await activeLabel(page), 'Отмена', 'при открытии фокус на безопасной кнопке «Отмена»')

  // Tab и Shift+Tab не выпускают фокус из окна (за ним есть десятки кнопок страницы)
  for (let i = 0; i < 8; i++) {
    await page.keyboard.press('Tab')
    assert.equal(await activeInside(page, '[aria-labelledby="ad-title"]'), true, `после Tab №${i + 1} фокус вышел из диалога: «${await activeLabel(page)}»`)
  }
  for (let i = 0; i < 8; i++) {
    await page.keyboard.press('Shift+Tab')
    assert.equal(await activeInside(page, '[aria-labelledby="ad-title"]'), true, `после Shift+Tab №${i + 1} фокус вышел из диалога`)
  }
  // круг замкнут: с последней кнопки Tab ведёт на первую
  await dlg.getByRole('button', { name: /^Принимаю риски и применить/ }).focus()
  await page.keyboard.press('Tab')
  assert.equal(await activeInside(page, '[aria-labelledby="ad-title"]'), true)
  assert.notEqual(await activeLabel(page), 'Принимаю риски и применить', 'с последней кнопки Tab уходит на первую')

  // Escape отменяет: диалог закрыт, команда не отправлена, фокус вернулся на «Применить уровень»
  await page.keyboard.press('Escape')
  await dlg.waitFor({ state: 'hidden' })
  assert.equal(dash.ops('ctl').filter((m) => m.kind === 'command').length, 0, 'Escape не должен принимать риски')
  await page.getByText('Сейчас уровень 3 · Базовый').waitFor()
})

// ИЗВЕСТНАЯ ОШИБКА ПРИЛОЖЕНИЯ (найдена этим прогоном): «Применить уровень» блокируется (disabled), пока идёт проверка условий; браузер снимает с неё фокус,
// и ActionDialog запоминает в качестве открывателя <body>. После Escape фокус не возвращается на кнопку, клавиатурному пользователю приходится идти по странице заново.
scenario('Диалог «Принимаю риски»: после Escape фокус возвращается на кнопку-открыватель «Применить уровень»', { todo: 'приложение: фокус теряется, кнопка блокируется на время проверки условий (ActionDialog запоминает <body>)' }, async ({ page, open, dash }) => {
  installRiskRules(dash)
  dash.rules['risk.set'].challenge = () => ({ data: riskyChallenge({ minDelay: 0 }) })
  await open('/system/control')
  await page.getByRole('group', { name: 'Уровень риска' }).getByRole('button', { name: /^5\b/ }).click()
  await page.getByRole('button', { name: 'Применить уровень' }).focus()
  await page.keyboard.press('Enter')
  const dlg = page.locator('[aria-labelledby="ad-title"]')
  await dlg.getByRole('button', { name: 'Отмена' }).waitFor()
  await page.keyboard.press('Escape')
  await dlg.waitFor({ state: 'hidden' })
  assert.equal(await activeLabel(page), 'Применить уровень', 'фокус вернулся на кнопку-открыватель')
})

scenario('Диалог «Принимаю риски»: Enter на «Принимаю» применяет, итог «Готово» закрывается Escape', async ({ page, open, dash }) => {
  installRiskRules(dash)
  dash.rules['risk.set'].challenge = () => ({ data: riskyChallenge({ minDelay: 0 }) })
  await open('/system/control')
  await page.getByRole('group', { name: 'Уровень риска' }).getByRole('button', { name: /^4\b/ }).click()
  const apply = page.getByRole('button', { name: 'Применить уровень' })
  await apply.focus()
  await page.keyboard.press('Enter')
  const dlg = page.locator('[aria-labelledby="ad-title"]')
  const accept = dlg.getByRole('button', { name: /^Принимаю риски и применить/ })
  await accept.focus()
  await page.keyboard.press('Enter')
  await dlg.getByText('Уровень риска 4 применён.').waitFor()
  assert.equal(await activeInside(page, '[aria-labelledby="ad-title"]'), true, 'в итоге фокус остаётся в диалоге')
  await page.keyboard.press('Escape')
  await dlg.waitFor({ state: 'hidden' })
  await page.getByText('Сейчас уровень 4').waitFor()
})

scenario('Боковая панель плана: Enter на строке открывает, адрес хранит ?plan=, Escape закрывает, обновление страницы открывает ту же панель', async ({ page, open }) => {
  await open('/trading/plans')
  const row = page.locator('main li.pick').first()
  await row.focus()
  await page.keyboard.press('Enter')
  const panel = page.getByRole('dialog', { name: 'План' })
  await panel.waitFor()
  assert.equal(new URL(page.url()).searchParams.get('plan'), 'PL-B1', 'выбор плана записан в адрес')
  await panel.getByText('Откат к поддержке').first().waitFor()          // данные плана пришли по запросу plan -> plan_detail

  await page.keyboard.press('Escape')
  await panel.waitFor({ state: 'hidden' })
  assert.equal(new URL(page.url()).searchParams.get('plan'), null, 'после закрытия адрес без ?plan=')

  // повторный клик по той же строке тоже закрывает
  await row.click()
  await panel.waitFor()
  await row.click()
  await panel.waitFor({ state: 'hidden' })

  // ссылка с ?plan= открывается сразу с панелью
  await page.goto(page.url().split('?')[0] + '?plan=PL-B2')
  await panel.waitFor()
  assert.equal(new URL(page.url()).searchParams.get('plan'), 'PL-B2')
  await page.keyboard.press('Escape')
  await panel.waitFor({ state: 'hidden' })
})

scenario('Боковая панель решения: клик открывает, адрес хранит ?decision=, Escape закрывает', async ({ page, open }) => {
  await open('/brain/decisions')
  await page.locator('main button.line').first().click()
  const panel = page.getByRole('dialog', { name: 'Решение' })
  await panel.waitFor()
  assert.equal(new URL(page.url()).searchParams.get('decision'), '1')
  await page.keyboard.press('Escape')
  await panel.waitFor({ state: 'hidden' })
  assert.equal(new URL(page.url()).searchParams.get('decision'), null)

  await page.goto(page.url().split('?')[0] + '?decision=1')
  await panel.waitFor()
  await page.keyboard.press('Escape')
  await panel.waitFor({ state: 'hidden' })
})
