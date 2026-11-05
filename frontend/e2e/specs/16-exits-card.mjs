/**
 * Сценарий 16. Страница «Система · Контроль»: карточка «Политика выхода» (R4, только чтение, владельческая надстройка).
 * Фейковый сервер отвечает на exits_status темой exits_status_detail. Проверки: полное состояние (версия, что видит исполнитель,
 * выключатель, продвижение, испытания, сторож), повторный запрос кнопкой «обновить», состояние без базы (ok:false, файловые поля
 * остаются), 390 px без горизонтальной прокрутки.
 */
import assert from 'node:assert/strict'
import { scenario, waitFor } from '../lib/harness.mjs'

const TS = 1793830200

const FULL = {
  ok: true,
  ts: TS,
  db: 'OK',
  version: {
    seq: 42, ver: 'X-0042', parent: 'X-0041', reason_code: 'PROMOTE', policy_h: 'policy-h', floor_h: 'floor-h', actor: 'CYCLE', ts: TS, numbers: { n: 12 },
  },
  versions: 42,
  effective: { ver: 'X-0042', policy_h: 'policy-h', why: 'OK' },
  snapshot: { ver: 'X-0042', policy_h: 'policy-h', floor_h: 'floor-h', ts: TS },
  lease: { ts: TS, ver: 'X-0042', policy_h: 'policy-h', by: 'promote' },
  switch: { apply: true, source: 'default', ts: null, actor: null },
  promotion: { mode: 'auto' },
  trials: {
    open: 2,
    closed: 5,
    rows: [{ trial_id: 'T-0009', var_id: 'v-1', var_ver: 2, base_ver: 'X-0038', state: 'closed', verdict: 'promote', start_ts: TS, deadline_ts: TS, closed_ts: TS, numbers: null }],
  },
  watch: { ts: TS, ver: 'X-0042', prev: 'X-0041', codes: ['EXIT_SIM_GAP'], points: 12, last_point: null },
  config: { errors: [] },
}

const NO_DB = {
  ok: false,
  ts: TS,
  db: 'NO_DB',
  error: 'база знаний ещё не создана',
  version: null,
  versions: 0,
  effective: null,
  snapshot: null,
  lease: null,
  switch: { apply: false, source: 'broken', ts: null, actor: null },
  promotion: { mode: 'off' },
  trials: { open: 0, closed: 0, rows: [] },
  watch: null,
  config: { errors: ['config/exits.json: promotion.mode'] },
}

const card = (page) => page.getByRole('region', { name: 'Политика выхода' })

scenario('Политика выхода: карточка показывает версию, исполнителя, выключатель, продвижение и испытания', async ({ page, open, dash }) => {
  dash.replies.exits_status = async () => ({ topic: 'exits_status_detail', data: FULL })
  await open('/system/control')
  const c = card(page)
  await c.getByText('X-0042').first().waitFor()
  const t = await c.innerText()
  assert.match(t, /Политика выхода/)
  assert.match(t, /X-0042[\s\S]*продвинута/)
  assert.match(t, /Исполнитель видит/)
  assert.match(t, /всё сходится/)
  assert.match(t, /включено/)
  assert.match(t, /авто/)
  assert.match(t, /открыто 2, закрыто 5/)
  assert.match(t, /расхождение факта и симуляции \(EXIT_SIM_GAP\)/)
  assert.match(t, /Только чтение/)
  assert.ok(dash.ops('exits_status').length >= 1, 'запрос состояния отправлен')

  const before = dash.ops('exits_status').length
  await c.getByRole('button', { name: 'обновить' }).click()
  await waitFor(() => dash.ops('exits_status').length > before, 'повторный запрос кнопкой «обновить»')
})

scenario('Политика выхода без базы: причина и код видны, файловые поля не прячутся', async ({ page, open, dash }) => {
  dash.replies.exits_status = async () => ({ topic: 'exits_status_detail', data: NO_DB })
  await open('/system/control')
  const c = card(page)
  await c.getByText('база знаний ещё не создана').waitFor()
  const t = await c.innerText()
  assert.match(t, /NO_DB/)
  assert.match(t, /ВЫКЛЮЧЕНО/)
  assert.match(t, /файл повреждён, поэтому применение выключено/)
  assert.match(t, /версии не продвигаются автоматически/)
  assert.match(t, /config\/exits\.json: promotion\.mode/)
  assert.match(t, /Снимок[\s\S]*нет/)
  assert.ok(!/открыто 0/.test(t), 'нули из недоступной базы не выдаются за данные')
})

scenario('Политика выхода: на 390 px нет горизонтальной прокрутки, карточка видна', { viewport: { width: 390, height: 844 } }, async ({ page, open, dash }) => {
  dash.replies.exits_status = async () => ({ topic: 'exits_status_detail', data: FULL })
  await open('/system/control')
  const c = card(page)
  await c.getByText('X-0042').first().waitFor()
  const m = await page.evaluate(() => ({ sw: document.scrollingElement.scrollWidth, cw: document.scrollingElement.clientWidth }))
  assert.ok(m.sw <= m.cw, `горизонтальная прокрутка ${m.sw} > ${m.cw}`)
  assert.ok(await c.isVisible())
})
