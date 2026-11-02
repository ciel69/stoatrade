/**
 * Сценарий 13. Настройки: три списка и три формы в окнах. Профиль модели (создание и правка одной формой), роль (основная и запасные), клавиатура окна (Esc, возврат фокуса, фокус-ловушка),
 * чистота экрана (нет «Для специалиста», эмодзи), адаптив 390 px. Сервер фейковый: brain.profile и brain.role применяются в теме settings через моки фронта.
 */
import assert from 'node:assert/strict'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { scenario, waitFor } from '../lib/harness.mjs'
import { calmChallenge } from '../lib/fake-dash.mjs'
import { SRC_DIR } from '../lib/env.mjs'

const mock = await import(pathToFileURL(path.join(SRC_DIR, 'entities/role/lib/mock.ts')).href)
const SETTINGS = '/system/settings'
const EMOJI = /[\p{Emoji_Presentation}\p{Extended_Pictographic}]/u
const box = (page) => page.locator('.box[role="dialog"]')
const commands = (dash) => dash.ops('ctl').filter((m) => m.kind === 'command')

function installRules(dash) {
  dash.rules['brain.profile'] = {
    challenge: () => ({ data: calmChallenge() }),
    command: (args, _m, d) => ({ final: { status: 'done', code: 'OK', msg: '' }, apply: () => d.patch('settings', (s) => mock.mockProfile(s, args)) }),
  }
  dash.rules['brain.role'] = {
    challenge: () => ({ data: calmChallenge() }),
    command: (args, _m, d) => ({ final: { status: 'done', code: 'OK', msg: '' }, apply: () => d.patch('settings', (s) => mock.mockChain(s, args.role, { primary: args.primary, fallback: args.fallback })) }),
  }
}

async function noOverflow(page, what) {
  const m = await page.evaluate(() => ({ sw: document.scrollingElement.scrollWidth, cw: document.scrollingElement.clientWidth }))
  assert.ok(m.sw <= m.cw, `${what}: горизонтальная прокрутка ${m.sw} px при окне ${m.cw} px`)
}

scenario('Экран настроек: три списка с кнопками «Изменить» и «Добавить», без блока «Для специалиста»', async ({ page, open }) => {
  await open(SETTINGS)
  for (const name of ['Кто думает', 'Профили моделей', 'Провайдеры']) await page.getByRole('heading', { name, level: 2 }).waitFor()
  assert.ok((await page.getByRole('button', { name: /^Изменить роль/ }).count()) >= 2, 'у каждой роли своя кнопка')
  assert.ok((await page.getByRole('button', { name: /^Изменить профиль/ }).count()) >= 2)
  assert.ok((await page.getByRole('button', { name: /^Изменить провайдера/ }).count()) >= 2)
  await page.getByRole('button', { name: 'Добавить профиль' }).waitFor()
  await page.getByRole('button', { name: 'Добавить провайдера' }).waitFor()
  const text = await page.locator('main').innerText()
  assert.ok(!/Для специалиста|Какую модель спросить|Что подключено/.test(text), 'лишних блоков нет')
  assert.ok(!EMOJI.test(text), 'эмодзи на странице')
})

scenario('Профиль: создание и правка одной формой, поля проверяются словами, параметры уходят в brain.profile', async ({ page, open, dash }) => {
  installRules(dash)
  await open(SETTINGS)
  await page.getByRole('button', { name: 'Добавить профиль' }).click()
  const dlg = page.getByRole('dialog', { name: 'Новый профиль', exact: true })
  await dlg.waitFor()
  const create = dlg.getByRole('button', { name: 'Создать профиль' })
  assert.equal(await create.isDisabled(), true, 'без названия не отправляется')
  assert.equal(await dlg.getByLabel('Провайдер').inputValue(), 'deepseek', 'по умолчанию первый включённый провайдер')
  await dlg.getByLabel('Название').fill('Быстрый чат')
  await dlg.getByText(/Служебное имя платформа построит сама: bystryy_chat/).waitFor()
  await dlg.getByLabel('Разброс ответов').fill('3')
  await dlg.getByText(/Число от 0 до 2/).waitFor()
  assert.equal(await create.isDisabled(), true)
  await dlg.getByLabel('Разброс ответов').fill('0,3')
  await dlg.getByLabel('Длина ответа').fill('2000')
  await dlg.getByLabel('Рассуждение').selectOption('low')
  await create.click()
  await waitFor(() => commands(dash).some((m) => m.action === 'brain.profile'), 'команда brain.profile')
  assert.deepEqual(commands(dash).pop().args, { op: 'set', id: 'bystryy_chat', provider: 'deepseek', model: 'flash', params: { temperature: 0.3, max_tokens: 2000, thinking: 'low' } })
  await dlg.waitFor({ state: 'hidden' })
  await page.getByText('bystryy_chat', { exact: true }).waitFor()

  // та же форма на «Изменить»: заполнена текущими значениями, уходит только изменённое
  await page.getByRole('button', { name: 'Изменить профиль bystryy_chat' }).click()
  const edit = page.getByRole('dialog', { name: 'Профиль «bystryy_chat»', exact: true })
  await edit.waitFor()
  assert.equal(await edit.getByLabel('Длина ответа').inputValue(), '2000')
  const save = edit.getByRole('button', { name: 'Сохранить', exact: true })
  assert.equal(await save.isDisabled(), true, 'пока ничего не изменено, сохранять нечего')
  await edit.getByLabel('Длина ответа').fill('2500')
  await save.click()
  await waitFor(() => commands(dash).filter((m) => m.action === 'brain.profile').length >= 2, 'вторая команда brain.profile')
  assert.deepEqual(commands(dash).pop().args, { op: 'set', id: 'bystryy_chat', params: { max_tokens: 2500 } })
  await edit.waitFor({ state: 'hidden' })
})

scenario('Роль: основной профиль и запасные в одной форме, «Применить» отправляет brain.role', async ({ page, open, dash }) => {
  installRules(dash)
  await open(SETTINGS)
  const btn = page.getByRole('button', { name: /^Изменить роль/ }).first()
  const label = (await btn.getAttribute('aria-label')).replace('Изменить роль ', '')
  await btn.click()
  const dlg = page.getByRole('dialog', { name: label, exact: true })
  await dlg.waitFor()
  const apply = dlg.getByRole('button', { name: 'Применить', exact: true })
  assert.equal(await apply.isDisabled(), true, 'пока ничего не изменено, применять нечего')
  await dlg.getByLabel('Профиль', { exact: true }).selectOption('ds_pro')
  await dlg.getByText(/Будет изменено: основная:/).waitFor()
  await dlg.getByLabel('Добавить запасную').selectOption('lm_qwen3_30b')
  await dlg.getByRole('button', { name: /Убрать из запасных: qwen3-30b/ }).waitFor()
  await apply.click()
  await waitFor(() => commands(dash).some((m) => m.action === 'brain.role'), 'команда brain.role')
  const a = commands(dash).pop().args
  assert.equal(a.op, 'chain')
  assert.equal(a.primary, 'ds_pro')
  assert.ok(a.fallback.includes('lm_qwen3_30b'))
  await dlg.getByText(/Применено/).first().waitFor()
  await dlg.getByRole('button', { name: 'Вернуть как было' }).waitFor()
  await dlg.getByText('данные обновились').waitFor()
})

scenario('Окно формы: Esc закрывает и возвращает фокус на кнопку, Tab и Shift+Tab не выходят из окна', async ({ page, open, dash }) => {
  installRules(dash)
  await open(SETTINGS)
  const btn = page.getByRole('button', { name: 'Изменить профиль ds_flash' })
  await btn.focus()
  await page.keyboard.press('Enter')
  const dlg = page.getByRole('dialog', { name: 'Профиль «ds_flash»', exact: true })
  await dlg.waitFor()
  assert.equal(await dlg.getAttribute('aria-modal'), 'true')
  for (let i = 0; i < 14; i += 1) {
    await page.keyboard.press('Tab')
    assert.equal(await dlg.evaluate((el) => el.contains(document.activeElement)), true, `фокус остался в окне после Tab ${i + 1}`)
  }
  for (let i = 0; i < 3; i += 1) {
    await page.keyboard.press('Shift+Tab')
    assert.equal(await dlg.evaluate((el) => el.contains(document.activeElement)), true, 'фокус остался в окне после Shift+Tab')
  }
  await page.keyboard.press('Escape')
  await dlg.waitFor({ state: 'hidden' })
  assert.equal(await btn.evaluate((el) => el === document.activeElement), true, 'фокус вернулся на «Изменить»')
  await page.getByRole('button', { name: 'Изменить профиль ds_flash' }).click()
  await dlg.waitFor()
  await dlg.getByRole('button', { name: 'Закрыть', exact: true }).first().click()
  await dlg.waitFor({ state: 'hidden' })
})

scenario('Адаптив 390 px: формы профиля и роли без горизонтальной прокрутки, у каждого поля подпись', { viewport: { width: 390, height: 844 } }, async ({ page, open }) => {
  await open(SETTINGS)
  await noOverflow(page, 'страница, 390 px')
  await page.getByRole('button', { name: 'Добавить профиль' }).click()
  const dlg = page.getByRole('dialog', { name: 'Новый профиль', exact: true })
  await dlg.waitFor()
  await noOverflow(page, 'форма профиля, 390 px')
  let unlabeled = await dlg.evaluate((el) => [...el.querySelectorAll('input:not([type=hidden]), select')].filter((n) => !n.labels?.length).map((n) => n.id))
  assert.deepEqual(unlabeled, [])
  const shotDir = process.env.E2E_SHOTS
  if (shotDir) await page.screenshot({ path: path.join(shotDir, 'profile-form-390.png') })
  await page.keyboard.press('Escape')
  await dlg.waitFor({ state: 'hidden' })
  await page.getByRole('button', { name: /^Изменить роль/ }).first().click()
  const role = box(page)
  await role.waitFor()
  await noOverflow(page, 'форма роли, 390 px')
  unlabeled = await role.evaluate((el) => [...el.querySelectorAll('input:not([type=hidden]), select')].filter((n) => !n.labels?.length).map((n) => n.id))
  assert.deepEqual(unlabeled, [])
  if (shotDir) await page.screenshot({ path: path.join(shotDir, 'role-form-390.png') })
})
