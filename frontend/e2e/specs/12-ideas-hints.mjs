/**
 * Сценарий 12. Страница «Идеи» для новичка: сетап по-русски, заметка сканера по-русски (оригинал в подробностях), понятная строка про заметку без уровня,
 * стилизованные подсказки вместо нативных title, раскладка шапки карточки не съехала, подсказка не выбирает карточку, обе темы.
 */
import assert from 'node:assert/strict'
import { scenario } from '../lib/harness.mjs'

const tooltip = (page) => page.locator('[role="tooltip"]:not(.tip-pop-leave-active)')
const card = (page, sym) => page.locator('section.ideas li.card', { has: page.getByText(sym, { exact: true }) }).first()

for (const theme of ['dark', 'light']) {
  scenario(`Идеи, 390 px, ${theme === 'dark' ? 'тёмная' : 'светлая'} тема: сетап и заметка по-русски, подсказки вместо title, шапка у краёв`,
    { viewport: { width: 390, height: 844 }, colorScheme: theme }, async ({ page, open }) => {
      await open('/trading/ideas')
      const eth = card(page, 'ETH')
      await eth.waitFor()

      const titles = await page.locator('section.ideas [title]:not([title=""])').evaluateAll((n) => n.map((x) => x.tagName + ':' + x.getAttribute('title')))
      assert.deepEqual(titles, [], 'на карточках идей не осталось нативных title')

      const text = await eth.innerText()
      assert.ok(text.includes('откат') && !text.includes('откат по тренду'), 'на телефоне сетап показан коротким русским словом')
      assert.ok(!text.includes('trend_pullback'), 'сырой код сетапа на карточке не виден')
      assert.ok(text.includes('Сопротивление выше цены'), 'заметка сканера переведена')
      assert.ok(!text.includes('res above price'), 'английская заметка не осталась')

      const nil = card(page, 'NIL')
      const nilText = await nil.innerText()
      assert.match(nilText, /Заметка без цены входа\. Пока нет уровня и стопа, система за ней не следит и сделку по ней не откроет\./)
      assert.ok(!/разбудит/.test(nilText), 'жаргон «разбудит» убран')

      // раскладка: процент и срок у правого края, монета у левого, шапка в границах карточки
      const geo = await page.evaluate(() => [...document.querySelectorAll('section.ideas li.card')].map((li) => {
        const b = li.querySelector('.body').getBoundingClientRect()
        const head = li.querySelector('.head')
        const h = head.getBoundingClientRect()
        const last = head.lastElementChild.getBoundingClientRect()
        const sym = li.querySelector('.sym').getBoundingClientRect()
        return { bodyRight: b.right, bodyLeft: b.left, headRight: h.right, lastRight: last.right, symLeft: sym.left }
      }))
      for (const g of geo) {
        assert.ok(Math.abs(g.bodyRight - g.lastRight) <= 1, `срок у правого края карточки: ${g.bodyRight} против ${g.lastRight}`)
        assert.ok(Math.abs(g.symLeft - g.bodyLeft) <= 1, 'монета у левого края')
        assert.ok(g.lastRight <= g.headRight + 1, 'шапка не шире карточки')
      }

      // подсказки: сетап, процент, срок; касание не выбирает карточку
      await eth.getByRole('button', { name: 'откат', exact: true }).click()
      await tooltip(page).waitFor()
      const tag = await tooltip(page).innerText()
      assert.match(tag, /Откат по тренду/)
      assert.match(tag, /по ходу тренда/)
      assert.match(tag, /trend_pullback/, 'исходный код тега остаётся в подсказке')
      const box = await page.evaluate(() => { const r = document.querySelector('[role="tooltip"]').getBoundingClientRect(); return { l: r.left, r: r.right, vw: document.documentElement.clientWidth } })
      assert.ok(box.l >= 0 && box.r <= box.vw, 'подсказка помещается в окно')
      assert.ok(!page.url().includes('idea='), 'касание подсказки не открыло панель идеи')
      await page.keyboard.press('Escape')
      await tooltip(page).waitFor({ state: 'detached' })

      await eth.locator('.dist').click()
      await tooltip(page).waitFor()
      const dist = await tooltip(page).innerText()
      assert.match(dist, /Цена на 0\.84% ниже уровня/)
      assert.match(dist, /вырасти/)
      await page.mouse.click(5, 400)
      await tooltip(page).waitFor({ state: 'detached' })

      await eth.locator('.ttl').click()
      await tooltip(page).waitFor()
      assert.match(await tooltip(page).innerText(), /истечёт, если цена не дойдёт до уровня/)
      await page.mouse.click(5, 400)
      await tooltip(page).waitFor({ state: 'detached' })

      assert.ok(await page.evaluate(() => document.scrollingElement.scrollWidth <= document.scrollingElement.clientWidth), 'нет горизонтальной прокрутки')
    })
}

scenario('Идеи на десктопе: подписи под полосой с пояснением, клик по карточке по-прежнему открывает панель с оригиналом заметки, строки исходов с подсказками', async ({ page, open }) => {
  await open('/trading/ideas')
  const eth = card(page, 'ETH')
  await eth.waitFor()

  await eth.getByRole('button', { name: /^2290/ }).hover()
  await tooltip(page).waitFor()
  assert.match(await tooltip(page).innerText(), /Стоп/)
  await page.mouse.move(700, 880)
  await tooltip(page).waitFor({ state: 'detached' })

  await eth.getByRole('button', { name: 'шорт' }).hover()
  await tooltip(page).waitFor()
  assert.match(await tooltip(page).innerText(), /ставка на падение|Ставка на падение|Шорт/)
  await page.mouse.move(700, 880)
  await tooltip(page).waitFor({ state: 'detached' })

  await eth.click({ position: { x: 3, y: 3 } })        // верхний левый угол карточки: там только кнопка на всю карточку, полоса и подсказки не мешают
  const panel = page.getByRole('dialog').or(page.locator('aside:has-text("Оригинал заметки")')).first()
  await page.getByText('Оригинал заметки').waitFor()
  const t = await page.locator('body').innerText()
  assert.ok(t.includes('res above price'), 'оригинал заметки сохранён в подробностях')
  assert.ok(t.includes('Схема входа'), 'строка «Тег» переименована в «Схема входа»')
  void panel
  await page.keyboard.press('Escape')

  await page.getByRole('button', { name: /Не сработали/ }).or(page.getByText('Не сработали')).first().click()
  const row = page.locator('section.ideas li.row').first()
  await row.waitFor()
  assert.deepEqual(await page.locator('section.ideas [title]:not([title=""])').evaluateAll((n) => n.length), 0, 'в строках исходов нет нативных title')
  await row.getByRole('button', { name: /^[+−-]/ }).first().hover()
  await tooltip(page).waitFor()
  assert.match(await tooltip(page).innerText(), /Что было бы через 60 мин/)
  await row.click({ position: { x: 3, y: 3 } })
  await page.getByText('Замеры').or(page.getByText('После издержек')).first().waitFor()
})
