/**
 * Сценарий 4. Старт дня: окно запуска, выбор режима, предупреждения принимаются общим диалогом, прогресс запуска и итог «День запущен».
 * Реальные деньги: серый вариант при выключенном выключателе (ctl.live_start=false), усиленный диалог при включённом, ворота не пройдены: «Начать торговлю» недоступна.
 */
import assert from 'node:assert/strict'
import { scenario } from '../lib/harness.mjs'
import { installDayStartRules, riskyChallenge } from '../lib/ctl-rules.mjs'

const startWindow = (page) => page.locator('[aria-labelledby="sd-title"]')
const riskDialog = (page) => page.locator('[aria-labelledby="ad-title"]')
const openWindow = async (page) => {
  await page.getByRole('button', { name: /Старт торговли/ }).first().click()
  const w = startWindow(page)
  await w.waitFor()
  return w
}
const begin = (w) => w.getByRole('button', { name: 'Начать торговлю' })

scenario('Старт дня (paper): режим, предупреждения общим диалогом, ход запуска без перекрытия, итог «День запущен»', async ({ page, open, dash }) => {
  installDayStartRules(dash)
  await open('/')
  const w = await openWindow(page)
  await w.locator('#sd-title').getByText('Старт торговли').waitFor()

  // условия запрашиваются для выбранного режима; по умолчанию бумага
  await w.getByText('Проверки пройдены: 8 из 8').waitFor()
  await w.getByText('Виртуальные сделки, реальных денег нет.').waitFor()
  await w.getByText('Есть одно предупреждение').waitFor()
  assert.deepEqual(dash.ops('ctl').map((m) => [m.kind, m.action, m.args.mode]), [['challenge', 'day.start', 'paper']])

  // смена режима запрашивает условия заново
  await w.getByRole('button', { name: 'Тестнет' }).click()
  await w.getByText('Настоящая биржа, но не настоящие деньги.').waitFor()
  await w.getByRole('button', { name: 'Бумага' }).click()
  await w.getByText('Виртуальные сделки, реальных денег нет.').waitFor()
  assert.deepEqual(dash.ops('ctl').map((m) => m.args.mode), ['paper', 'testnet', 'paper'])

  // «Начать торговлю»: предупреждения принимаются общим диалогом
  await begin(w).click()
  const risk = riskDialog(page)
  await risk.waitFor()
  await risk.getByRole('heading', { name: /Старт торгового дня/ }).waitFor()
  await risk.getByText('Что нужно принять').waitFor()
  await risk.getByText('На бумаге пока мало опыта', { exact: false }).waitFor()
  await risk.getByRole('button', { name: /^Принимаю риски и применить/ }).click()

  // ход запуска показывает окно дня, общее окно поверх него не появляется
  await w.locator('#sd-title').getByText('Запускаю день').waitFor()
  await w.getByRole('progressbar', { name: 'Ход запуска дня' }).waitFor()
  await risk.waitFor({ state: 'hidden', timeout: 2000 }).catch(() => assert.fail('общее окно «Принимаю риски» закрыло анимацию запуска'))        // короткое затухание допустимо
  await w.getByText(/шаг \d+ из 9/).waitFor()
  assert.equal(await risk.count(), 0, 'общее окно появилось во время запуска')

  await w.locator('#sd-title').getByText('День запущен').waitFor()
  await w.getByText('Торговый день:').waitFor()
  assert.equal(await risk.count(), 0)
  await w.getByRole('button', { name: 'Готово' }).click()
  await w.waitFor({ state: 'hidden' })

  const cmd = dash.ops('ctl').filter((m) => m.kind === 'command')
  assert.equal(cmd.length, 1)
  assert.deepEqual(cmd[0].args, { mode: 'paper', session: 'day', accepted: ['W1'], allow_amber: false })
  assert.equal(cmd[0].nonce, 'n-paper')
  // шапка и кнопка старта отражают, что день идёт
  await page.getByRole('button', { name: /Торговля идёт/ }).first().waitFor()
})

scenario('Старт дня (paper): без предупреждений день стартует сразу, без диалога «Принимаю риски»', async ({ page, open, dash }) => {
  installDayStartRules(dash, { stepMs: 80 })
  dash.rules['day.start'].challenge = () => ({ data: { gates: [{ id: 'H1', name: 'KILL чист', ok: true }], warnings: [], pre_amber: [], danger: false, phrases: [], blocked: false, nonce: 'n-calm', min_delay: 0, expires_in: 120 } })
  await open('/')
  const w = await openWindow(page)
  await w.getByText('Проверки пройдены: 1 из 1').waitFor()
  await begin(w).click()
  await w.locator('#sd-title').getByText('День запущен').waitFor()
  assert.equal(await riskDialog(page).count(), 0)
})

scenario('Старт дня: жёсткие условия не пройдены, «Начать торговлю» недоступна и причина названа', async ({ page, open, dash }) => {
  installDayStartRules(dash)
  dash.rules['day.start'].challenge = () => ({ ok: false, code: 'E_GATE', msg: 'условия не выполнены', data: { gates: [{ id: 'H1', name: 'KILL чист', ok: true }, { id: 'H6', name: 'Мозг доступен', ok: false, msg: 'нет heartbeat сервиса brain' }], warnings: [], pre_amber: [], danger: false, blocked: true } })
  await open('/')
  const w = await openWindow(page)
  await w.getByText('Не прошло проверок: 1').waitFor()
  assert.equal(await begin(w).isDisabled(), true, 'ворота не пройдены: старт недоступен')
  await w.getByRole('alert').filter({ hasText: /./ }).first().waitFor()
  assert.equal(dash.ops('ctl').filter((m) => m.kind === 'command').length, 0)
})

// ---- реальные деньги

const live = (w) => w.getByRole('button', { name: 'Реальные деньги' })

scenario('Live: при выключенном выключателе (ctl.live_start=false) вариант серый, причина словами, запросов live нет', async ({ page, open, dash }) => {
  installDayStartRules(dash)
  await open('/')
  const w = await openWindow(page)
  await w.getByText('Проверки пройдены: 8 из 8').waitFor()
  assert.match(await live(w).getAttribute('class'), /muted/, 'вариант «Реальные деньги» серый')
  await live(w).click()
  await w.getByText('Запуск на реальные деньги из этого дашборда выключен владельцем').waitFor()
  assert.equal(await begin(w).isDisabled(), true)
  assert.equal(dash.ops('ctl').filter((m) => m.args?.mode === 'live').length, 0, 'для недоступного live запросы к платформе не уходят')
  assert.equal(await w.getByText('Жёсткие проверки допуска').count(), 0)

  // владелец включил выключатель: тема ctl обновилась, вариант стал доступным без перезагрузки
  dash.patch('ctl', (c) => { c.live_start = true })
  await page.waitForFunction(() => !document.querySelector('.seg .opt.muted'))
  await live(w).click()
  await w.getByText('Жёсткие проверки допуска').waitFor()
})

scenario('Live: при включённом выключателе усиленный текст, проверки допуска и диалог с фразой', async ({ page, open, dash }) => {
  installDayStartRules(dash, { stepMs: 80, warnings: [{ id: 'W1', code: 'W_PAPER', msg: 'сделок 18 из рекомендованных 30; дней 5 из 7' }] })
  dash.patch('ctl', (c) => { c.live_start = true })
  await open('/')
  const w = await openWindow(page)
  await live(w).click()
  await w.locator('#sd-title').getByText('Старт торговли на реальные деньги').waitFor()
  await w.getByText('Будут торговаться реальные деньги').waitFor()
  await w.getByRole('list', { name: 'Жёсткие проверки допуска' }).waitFor()
  await w.getByText('всё готово').waitFor()
  assert.equal(dash.ops('ctl').at(-1).args.mode, 'live')
  assert.equal(await begin(w).isEnabled(), true)

  await begin(w).click()
  const risk = riskDialog(page)
  await risk.getByRole('heading', { name: 'Запуск торговли на реальные деньги' }).waitFor()
  await risk.getByText('Это реальные деньги: потери реальны').waitFor()
  await risk.getByRole('button', { name: /^Принимаю риски и применить/ }).click()
  await w.locator('#sd-title').getByText('День запущен').waitFor()
  const cmd = dash.ops('ctl').filter((m) => m.kind === 'command').at(-1)
  assert.equal(cmd.args.mode, 'live')
  assert.deepEqual(cmd.phrases, ['ТОРГУЮ LIVE 2026-11-02 УРОВЕНЬ 3'], 'фраза подтверждения подставляется из challenge, вводить её не нужно')
  assert.equal(cmd.nonce, 'n-live')
})

scenario('Live: ворота H5 и H6 не пройдены, «Начать торговлю» недоступна', async ({ page, open, dash }) => {
  installDayStartRules(dash)
  dash.patch('ctl', (c) => { c.live_start = true })
  const gates = ['H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'H7', 'H8'].map((id) => ({ id, name: id, ok: !['H5', 'H6'].includes(id), msg: id === 'H5' ? 'убыток за сутки 3.20 из 3.00 USDT' : id === 'H6' ? 'нет heartbeat сервиса brain' : '' }))
  dash.rules['day.start'].challenge = () => ({ ok: false, code: 'E_GATE', msg: 'условия не выполнены', data: riskyChallenge({ gates, blocked: true, warnings: [], phrases: ['ТОРГУЮ LIVE 2026-11-02 УРОВЕНЬ 3'] }) })
  await open('/')
  const w = await openWindow(page)
  await live(w).click()
  await w.getByText('не готово: 2').waitFor()
  assert.equal(await begin(w).isDisabled(), true, 'пока ворота не пройдены, старт недоступен')
  assert.equal(dash.ops('ctl').filter((m) => m.kind === 'command').length, 0)
})

scenario('Live: без расширенного режима вариант серый и называет причину', { hello: { enabled: true, csrf: 'e2e-csrf', full: false, modes: ['day.start', 'day.stop'] } }, async ({ page, open, dash }) => {
  installDayStartRules(dash)
  dash.patch('ctl', (c) => { c.live_start = true })
  await open('/')
  const w = await openWindow(page)
  await live(w).click()
  await w.getByText('только в расширенном режиме управления').waitFor()
  assert.equal(await begin(w).isDisabled(), true)
})
