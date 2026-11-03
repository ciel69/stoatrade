/**
 * Сценарий 11. Подсказки на карточках планов и позиций: стилизованная подсказка вместо нативного title, понятные тексты,
 * открытие касанием/наведением/клавиатурой, Esc, помещается в окно 390 px, обе темы.
 */
import assert from 'node:assert/strict'
import { scenario } from '../lib/harness.mjs'

const restingPlan = (t) => {
  for (const list of [t.plans.items, t.home.plans.near]) list[0].entry_state = 'resting'
}

const tooltip = (page) => page.locator('[role="tooltip"]:not(.tip-pop-leave-active)')
const inViewport = async (page) => page.evaluate(() => {
  const r = document.querySelector('[role="tooltip"]').getBoundingClientRect()
  return { l: r.left, r: r.right, vw: document.documentElement.clientWidth }
})

for (const theme of ['dark', 'light']) {
  scenario(`Подсказки, 390 px, ${theme === 'dark' ? 'тёмная' : 'светлая'} тема: «заявка» и «подтянут» объяснены, нативных title нет`,
    { viewport: { width: 390, height: 844 }, colorScheme: theme, topics: restingPlan }, async ({ page, open }) => {
      await open('/')
      const plans = page.locator('li', { has: page.getByText('PL-B1').or(page.getByText('SOL', { exact: true })) }).first()
      await plans.waitFor()

      const titles = await page.locator('li:has(.rs) [title]:not([title=""]), li:has(.rs)[title]:not([title=""])').evaluateAll((n) => n.map((x) => x.tagName + ':' + x.getAttribute('title')))
      assert.deepEqual(titles, [], 'на строках планов и позиций не осталось нативных title')
      assert.equal(await page.getByText('лимит стоит').count(), 0, 'старая подпись «лимит стоит» убрана')

      // раскладка шапки: процент и таймер у правого края карточки, монета и чипы слева
      const geo = await page.evaluate(() => {
        const li = [...document.querySelectorAll('li:has(.rs)')].find((x) => x.querySelector('.timer'))
        const row = li.getBoundingClientRect()
        const last = li.querySelector('.head').lastElementChild.getBoundingClientRect()
        const dist = li.querySelector('.dist').getBoundingClientRect()
        const first = li.querySelector('.sym').getBoundingClientRect()
        return { rowRight: row.right, lastRight: last.right, distLeft: dist.left, firstLeft: first.left, rowLeft: row.left }
      })
      assert.ok(geo.rowRight - geo.lastRight <= 1, `таймер у правого края карточки: ${geo.rowRight} против ${geo.lastRight}`)
      assert.ok(geo.firstLeft - geo.rowLeft <= 1, 'монета у левого края')
      assert.ok(geo.distLeft > geo.firstLeft + 120, `процент не прижат к чипам слева: ${geo.distLeft}`)

      const overflow = await page.evaluate(() => [...document.querySelectorAll('li:has(.rs) .head')].filter((h) => h.lastElementChild.getBoundingClientRect().right > h.getBoundingClientRect().right + 1).length)
      assert.equal(overflow, 0, 'шапка ни одной карточки не шире самой карточки')

      const chip = page.getByRole('button', { name: 'заявка', exact: true })
      await chip.click()
      await tooltip(page).waitFor()
      const text = await tooltip(page).innerText()
      assert.match(text, /уже выставлена на бирже/)
      assert.match(text, /деньги не рискуются/)
      assert.equal(await chip.getAttribute('aria-describedby'), await tooltip(page).getAttribute('id'))
      const box = await inViewport(page)
      assert.ok(box.l >= 0 && box.r <= box.vw, `подсказка помещается в окно: ${box.l}..${box.r} из ${box.vw}`)

      await page.keyboard.press('Escape')
      await tooltip(page).waitFor({ state: 'detached' })

      const moved = page.getByRole('button', { name: 'подтянут', exact: true })
      await moved.click()
      await tooltip(page).waitFor()
      assert.match(await tooltip(page).innerText(), /прибыль зафиксирована/)
      await page.mouse.click(5, 400)
      await tooltip(page).waitFor({ state: 'detached' })

      assert.ok(await page.evaluate(() => document.scrollingElement.scrollWidth <= document.scrollingElement.clientWidth), 'нет горизонтальной прокрутки')
    })
}

scenario('Подсказки на десктопе: наведение на «лонг» и фокус с клавиатуры показывают пояснение, вынесенные подробности плана у названия монеты', { topics: restingPlan }, async ({ page, open }) => {
  await open('/')
  const side = page.getByRole('button', { name: /^лонг/ }).first()
  await side.waitFor()
  await side.hover()
  await tooltip(page).waitFor()
  assert.match(await tooltip(page).innerText(), /Ставка на рост|ставка на рост|Прибыль, если цена вырастет/)
  await page.mouse.move(700, 800)
  await tooltip(page).waitFor({ state: 'detached' })

  await side.focus()
  await page.keyboard.press('Tab')
  await page.keyboard.press('Shift+Tab')
  await tooltip(page).waitFor()
  await page.keyboard.press('Escape')
  await tooltip(page).waitFor({ state: 'detached' })

  const sym = page.getByRole('button', { name: 'SOL', exact: true }).first()
  await sym.hover()
  await tooltip(page).waitFor()
  const t = await tooltip(page).innerText()
  for (const label of ['Идея', 'Риск', 'Выигрыш к риску (RR)']) assert.ok(t.includes(label), `в подробностях плана есть «${label}»`)
})
