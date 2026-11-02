/**
 * Сценарий 5. Остановка дня и снятие аварийной остановки (KILL): блок виден при KILL в теме ctl, кнопка только при clear_kill=true, диалог, после успеха блок исчезает;
 * при clear_kill=false вместо кнопки заметка с командой для терминала.
 */
import assert from 'node:assert/strict'
import { leaveDialog, scenario } from '../lib/harness.mjs'
import { installDayStopRules, installKillRules } from '../lib/ctl-rules.mjs'
import { makeRunning } from '../lib/fixtures.mjs'

const startWindow = (page) => page.locator('[aria-labelledby="sd-title"]')
const riskDialog = (page) => page.locator('[aria-labelledby="ad-title"]')

const withKill = ({ clear }) => (topics) => {
  topics.ctl = {
    ...topics.ctl, kill: { flatten: false, reason: 'ручная остановка' }, clear_kill: clear,
    platform: { state: 'halted', mode: 'paper', session: null, health: 'red', day_id: null, trading: false, observe: false },
  }
}

scenario('Остановка дня: окно дня, общий диалог, ход завершения и итог', { topics: makeRunning }, async ({ page, open, dash }) => {
  installDayStopRules(dash)
  await open('/')
  await page.getByRole('button', { name: /Торговля идёт/ }).first().click()
  const w = startWindow(page)
  await w.locator('#sd-title').getByText('Торговый день идёт').waitFor()
  await w.getByRole('button', { name: 'Остановить день' }).click()

  const risk = riskDialog(page)
  await risk.getByRole('heading', { name: 'Остановить торговый день' }).waitFor()
  await risk.getByText('Открытые позиции останутся под стопами').waitFor()
  await risk.getByRole('button', { name: /^Принимаю риски и применить/ }).click()

  // ход завершения показывает окно дня, общее окно его не закрывает
  await w.locator('#sd-title').getByText('Завершаю день').waitFor()
  await risk.waitFor({ state: 'hidden', timeout: 2000 }).catch(() => assert.fail('общее окно закрыло ход завершения дня'))
  // итог: платформа остановлена, окно возвращается к выбору режима
  await w.locator('#sd-title').getByText('Старт торговли').waitFor()
  const cmd = dash.ops('ctl').find((m) => m.kind === 'command')
  assert.equal(cmd.action, 'day.stop')
  assert.deepEqual(cmd.args.mode, 'soft')
  assert.deepEqual(cmd.args.accepted ?? [], [], 'у day.stop нет ключа accepted в схеме')
})

scenario('KILL: блок «Аварийная остановка» с кнопкой при clear_kill=true, диалог, после успеха блок исчезает', { topics: withKill({ clear: true }) }, async ({ page, open, dash }) => {
  installKillRules(dash)
  await open('/system/control')
  const block = page.locator('section.kr')
  await block.getByRole('heading', { name: 'Аварийная остановка включена' }).waitFor()
  await block.getByText('Причина: ручная остановка.').waitFor()
  await block.getByText('Перед снятием платформа проверит:').waitFor()
  await block.getByRole('button', { name: 'Снять аварийную остановку' }).click()

  const risk = riskDialog(page)
  await risk.getByRole('heading', { name: 'Снять аварийную остановку' }).waitFor()
  await risk.getByText('Защитная остановка перестанет действовать').waitFor()
  await risk.getByRole('button', { name: /^Принимаю риски и применить/ }).click()
  await risk.getByText('Аварийная остановка снята.').waitFor()

  // тема ctl показала, что KILL снят: блок с кнопкой исчезает
  await block.getByRole('button', { name: 'Снять аварийную остановку' }).waitFor({ state: 'hidden' })
  await block.getByRole('heading', { name: 'Аварийная остановка включена' }).waitFor({ state: 'hidden' })
  const cmd = dash.ops('ctl').find((m) => m.kind === 'command')
  assert.equal(cmd.action, 'kill.clear')
  await leaveDialog(page, risk)
  await risk.waitFor({ state: 'hidden' })                                          // окно «Готово» закрывается само, когда курсор ушёл
  await block.waitFor({ state: 'hidden', timeout: 15_000 })                        // и подтверждение «снята» тоже уходит
})

scenario('KILL: при clear_kill=false вместо кнопки заметка с командой для терминала', { topics: withKill({ clear: false }) }, async ({ page, open, dash }) => {
  installKillRules(dash)
  await open('/system/control')
  const block = page.locator('section.kr')
  await block.getByRole('heading', { name: 'Аварийная остановка включена' }).waitFor()
  await block.getByText('Снятие остановки кнопкой выключено в настройках платформы').waitFor()
  await block.locator('code').getByText('./tb exec resume').waitFor()
  await block.getByRole('button', { name: 'Скопировать команду' }).waitFor()
  assert.equal(await block.getByRole('button', { name: 'Снять аварийную остановку' }).count(), 0, 'кнопки быть не должно')
  assert.equal(dash.ops('ctl').length, 0)
  // кнопка старта в шапке заблокирована и объясняет причину
  const start = page.getByRole('button', { name: /Остановлено/ }).first()
  assert.equal(await start.getAttribute('aria-disabled'), 'true')
})

scenario('KILL: условия снятия не выполнены, диалог объясняет, что мешает, команда не уходит', { topics: withKill({ clear: true }) }, async ({ page, open, dash }) => {
  installKillRules(dash)
  dash.rules['kill.clear'].challenge = () => ({
    ok: false, code: 'E_GATE', msg: 'условия не выполнены',
    data: { blocked: true, danger: false, warnings: [], pre_amber: [], kill: { active: true, reason: 'ручная остановка', flatten: false, mode: 'paper' }, gates: [{ id: 'K1', name: 'Исполнитель на связи', ok: true, msg: 'исполнитель жив' }, { id: 'H2', name: 'Стоп на бирже у каждой позиции', ok: false, msg: 'позиции без стопа на бирже: ETHUSDT' }] },
  })
  await open('/system/control')
  await page.locator('section.kr').getByRole('button', { name: 'Снять аварийную остановку' }).click()
  const risk = riskDialog(page)
  await risk.getByText('Что мешает').waitFor()
  await risk.getByRole('button', { name: 'Закрыть' }).click()
  await risk.waitFor({ state: 'hidden' })
  assert.equal(dash.ops('ctl').filter((m) => m.kind === 'command').length, 0)
  await page.locator('section.kr').getByRole('heading', { name: 'Аварийная остановка включена' }).waitFor()
})
