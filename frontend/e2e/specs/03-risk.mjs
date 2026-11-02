/**
 * Сценарий 3. Смена уровня риска: повышение через единый диалог «Принимаю риски и применить», понижение без диалога, отказ сервера человеческим текстом.
 */
import assert from 'node:assert/strict'
import { leaveDialog, scenario } from '../lib/harness.mjs'
import { installRiskRules, riskyChallenge } from '../lib/ctl-rules.mjs'

const dialog = (page) => page.getByRole('dialog', { name: /Уровень риска/ })
const levels = (page) => page.getByRole('group', { name: 'Уровень риска' })
const pickLevel = (page, n) => levels(page).getByRole('button', { name: new RegExp(`^${n}\\b`) }).click()
const apply = (page) => page.getByRole('button', { name: 'Применить уровень' })

scenario('Уровень риска: повышение через диалог «Принимаю риски», новый уровень после обновления темы ctl', async ({ page, open, dash }) => {
  installRiskRules(dash, { applyDelayMs: 600 })
  await open('/system/control')
  await page.getByText('Сейчас уровень 3 · Базовый').waitFor()

  await pickLevel(page, 4)
  await page.getByText('3 · Базовый → 4 · Активный').waitFor()          // строка «было → станет»
  await page.getByText('Повышение риска платформа попросит подтвердить').waitFor()
  await apply(page).click()

  // единый диалог: риски списком, кнопка с паузой, потом «Принимаю риски и применить»
  const dlg = dialog(page)
  await dlg.waitFor()
  await dlg.getByRole('heading', { name: 'Уровень риска 4: Активный' }).waitFor()
  await dlg.getByText('Что нужно принять').waitFor()
  await dlg.getByText('вырастут').first().waitFor()
  const accept = dlg.getByRole('button', { name: /^Принимаю риски и применить/ })
  await accept.waitFor()
  assert.deepEqual(dash.ops('ctl').map((m) => m.kind), ['challenge'], 'до нажатия «Принимаю» уходит только запрос условий')
  assert.equal(dash.ops('ctl')[0].csrf, 'e2e-csrf', 'запрос несёт csrf соединения из hello')
  await page.getByRole('button', { name: 'Принимаю риски и применить' }).click()        // клик ждёт, пока пауза min_delay кончится и кнопка станет доступной

  await dlg.getByText('Готово').waitFor()
  await dlg.getByText('Уровень риска 4 применён.').waitFor()
  const cmd = dash.ops('ctl').find((m) => m.kind === 'command')
  assert.equal(cmd.action, 'risk.set')
  assert.equal(cmd.args.level, 4)
  assert.deepEqual(cmd.args.accepted, ['W_RISK_UP'], 'принятые предупреждения уходят в команде')
  assert.equal(cmd.nonce, 'n-risky', 'nonce из challenge возвращается как есть')

  // виджет показывает новый уровень, как только тема ctl его отразила
  await page.getByText('Сейчас уровень 4').waitFor()
  assert.equal((await levels(page).getByRole('button', { name: /^4\b/ }).innerText()).includes('сейчас'), true, 'у уровня 4 метка «сейчас»')
  await leaveDialog(page, dlg)                                                            // пока курсор над окном, оно не закрывается само
  await dlg.waitFor({ state: 'hidden' })                                                  // окно «Готово» закрывается через пару секунд после ухода курсора
})

scenario('Уровень риска: понижение применяется без диалога, итог уведомлением', async ({ page, open, dash }) => {
  installRiskRules(dash)
  await open('/system/control')
  await page.getByText('Сейчас уровень 3 · Базовый').waitFor()

  await pickLevel(page, 2)
  await page.getByText('3 · Базовый → 2 · Осторожный').waitFor()
  assert.equal(await page.getByText('Повышение риска платформа попросит подтвердить').count(), 0, 'у понижения нет подсказки про подтверждение')
  await apply(page).click()

  await page.getByText('Уровень риска 2 применён.').first().waitFor()                   // уведомление справа внизу
  await page.getByText('Сейчас уровень 2').waitFor()
  const kinds = dash.ops('ctl').map((m) => m.kind)
  assert.deepEqual(kinds, ['challenge', 'command'], 'без рисков команда уходит сразу после проверки условий')
  assert.equal(await dialog(page).count(), 0, 'диалога «Принимаю риски» не было')
  assert.equal(await page.getByRole('button', { name: /Принимаю риски/ }).count(), 0)
})

scenario('Уровень риска: отказ сервера при проверке условий показан человеческим текстом', async ({ page, open, dash }) => {
  installRiskRules(dash)
  dash.rules['risk.set'].challenge = () => ({ ok: false, code: 'E_RATE', msg: 'too many requests', data: null })
  await open('/system/control')
  await page.getByText('Сейчас уровень 3 · Базовый').waitFor()
  await pickLevel(page, 5)
  await apply(page).click()
  const toast = page.locator('.toast.bad')
  await toast.getByText(/Слишком частые попытки/).waitFor()
  assert.equal(await page.getByText('too many requests').count(), 0, 'сырой текст сервера не показывается')
  assert.equal(await page.getByText('E_RATE').count(), 0, 'код ошибки не показывается человеку')
  assert.equal(dash.ops('ctl').filter((m) => m.kind === 'command').length, 0, 'после отказа команда не отправляется')
  await page.getByText('Сейчас уровень 3 · Базовый').waitFor()
})

scenario('Уровень риска: отказ сервера после принятия рисков показан в диалоге, уровень не меняется', async ({ page, open, dash }) => {
  installRiskRules(dash)
  dash.rules['risk.set'].challenge = () => ({ data: riskyChallenge({ minDelay: 0 }) })
  dash.rules['risk.set'].command = () => ({ final: { status: 'failed', code: 'E_STATE', msg: 'state mismatch' } })
  await open('/system/control')
  await pickLevel(page, 5)
  await apply(page).click()
  const dlg = dialog(page)
  await dlg.getByRole('button', { name: /^Принимаю риски и применить/ }).click()
  await dlg.getByText('Действие не выполнено').waitFor()
  await dlg.getByText('Сейчас так сделать нельзя').waitFor()
  assert.equal(await dlg.getByText('state mismatch').filter({ visible: true }).count(), 0, 'сырой текст сервера спрятан от человека')
  await dlg.getByRole('button', { name: 'Закрыть' }).click()
  await dlg.waitFor({ state: 'hidden' })
  await page.getByText('Сейчас уровень 3 · Базовый').waitFor()
})

scenario('Уровень риска: при выключенном расширенном управлении кнопки уровня недоступны, причина названа', { hello: { enabled: true, csrf: 'e2e-csrf', full: false, modes: ['day.start', 'day.stop'] } }, async ({ page, open }) => {
  await open('/system/control')
  await page.getByText('Изменение уровня риска выключено').waitFor()
  assert.equal(await levels(page).getByRole('button', { name: /^4\b/ }).isDisabled(), true)
})
