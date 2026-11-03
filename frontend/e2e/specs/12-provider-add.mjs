/**
 * Сценарий 12. Форма провайдера в окне (одна форма на «Добавить» и «Изменить»): проверка полей словами, внутренняя сеть через единый диалог «Принимаю риски», переход к правке после создания,
 * список моделей у провайдера, добавление модели, проверка подключения (платный запрос, без выбора модели), правка названия, адреса и ограничений расхода, ошибки словами, адаптив 390 px.
 * Сервер фейковый: правила llm.provider ниже изображают платформу (контракт: SETTINGS_API.md), данные темы settings берутся из моков фронта.
 */
import assert from 'node:assert/strict'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { leaveDialog, scenario, waitFor } from '../lib/harness.mjs'
import { riskyChallenge } from '../lib/ctl-rules.mjs'
import { calmChallenge } from '../lib/fake-dash.mjs'
import { SRC_DIR } from '../lib/env.mjs'

const mock = await import(pathToFileURL(path.join(SRC_DIR, 'entities/role/lib/mock.ts')).href)
const SETTINGS = '/system/settings'
const RAW = /\b(auth|bad_body|bad_request|E_[A-Z_]+|undefined|NaN|null|\[object|op:|model_set)\b/
const EMOJI = /[\p{Emoji_Presentation}\p{Extended_Pictographic}]/u

/** Правила платформы для llm.provider: предупреждения при внутренней сети и смене адреса, итоги models/ping с данными, применение в теме settings. */
function installProviderRules(dash, { network = {} } = {}) {
  const pick = (args) => args.spec ?? args.patch ?? {}
  dash.rules['llm.provider'] = {
    challenge: (args) => {
      const blk = pick(args)
      if (blk.allow_private === true) return { data: riskyChallenge({ danger: true, warnings: [{ id: 'llm.allow_private', code: 'ALLOW_PRIVATE', msg: 'Разрешаются приватные и внутренние адреса: ключ и запросы могут уйти во внутреннюю сеть.' }] }) }
      if (args.op === 'update' && typeof blk.base_url === 'string') {
        return { data: riskyChallenge({ danger: false, warnings: [{ id: 'llm.key_new_url', code: 'KEY_NEW_URL', msg: 'Ключ провайдера уйдёт на новый адрес. Убедитесь, что это нужный сервис.', numbers: { host: 'new.example.com', changed: ['base_url'] } }] }) }
      }
      return { data: calmChallenge() }
    },
    command: (args, _m, d) => {
      const blk = pick(args)
      const needs = blk.allow_private === true ? 'llm.allow_private' : args.op === 'update' && typeof blk.base_url === 'string' ? 'llm.key_new_url' : null
      if (needs && !(args.accepted ?? []).includes(needs)) return { final: { status: 'rejected', code: 'E_WARN', msg: 'предупреждения не приняты' } }
      const done = (data = null, apply = null) => ({ final: { status: 'done', code: 'OK', msg: '', data }, apply })
      switch (args.op) {
        case 'add': return done(null, () => d.patch('settings', (s) => mock.mockProviderAdd(s, args)))
        case 'model_set': return done(null, () => d.patch('settings', (s) => mock.mockModelSet(s, args.id, args.model, args.spec.name)))
        case 'update': return done(typeof blk.base_url === 'string' ? { send_warning: { code: 'KEY_NEW_URL', host: 'new.example.com', msg: 'ключ провайдера теперь уходит на new.example.com' } } : null, () => d.patch('settings', (s) => mock.mockAddress(s, args.id, args.patch)))
        case 'models': {
          if (network.models) return done({ ok: false, ...network.models })
          const ids = Array.from({ length: 500 }, (_, i) => `acme/model-${String(i + 1).padStart(3, '0')}`)
          return done({ ok: true, ids, count: 640, truncated: true, source: 'models', detail: '' })
        }
        case 'ping': {
          if (network.ping) return done({ ok: false, paid: true, model: 'flash', ...network.ping })
          return done({ ok: true, ms: 840, model: args.model ?? 'flash', model_name: 'deepseek-flash', paid: true, finish: 'stop', usage: { in: 9, in_hit: 0, in_write: 0, out: 2, reasoning: 0 }, text: 'pong' })
        }
        default: return done()
      }
    },
  }
}

const fill = async (scope, label, value) => { await scope.getByLabel(label, { exact: false }).fill(value) }

/** Снимок страницы в папку из E2E_SHOTS (для просмотра глазами; без переменной ничего не делает). */
async function shot(page, name) {
  const dir = process.env.E2E_SHOTS
  if (dir) await page.screenshot({ path: path.join(dir, `${name}.png`) }).catch(() => {})
}

async function noOverflow(page, what) {
  const m = await page.evaluate(() => ({ sw: document.scrollingElement.scrollWidth, cw: document.scrollingElement.clientWidth }))
  assert.ok(m.sw <= m.cw, `${what}: горизонтальная прокрутка ${m.sw} px при окне ${m.cw} px`)
}

const openEdit = async (page, name) => {
  await page.getByRole('button', { name: `Изменить провайдера ${name}` }).click()
  const dlg = page.locator('.box[role="dialog"]')   // окно формы одно; по имени его не найти: название меняется при сохранении
  await dlg.getByRole('heading', { name, level: 2, exact: true }).waitFor()
  return dlg
}

scenario('Новый провайдер: проверка полей словами, внутренняя сеть через единый диалог, после создания форма переходит к правке', async ({ page, open, dash }) => {
  installProviderRules(dash)
  await open(SETTINGS)
  const text0 = await page.locator('main').innerText()
  assert.ok(!/Для специалиста|Какую модель спросить|Что подключено/.test(text0), 'старые блоки убраны')
  await page.getByRole('button', { name: 'Добавить провайдера' }).click()
  const dlg = page.getByRole('dialog', { name: 'Новый провайдер', exact: true })
  await dlg.waitFor()
  const create = dlg.getByRole('button', { name: 'Создать провайдера', exact: true })
  assert.equal(await create.isDisabled(), true, 'пустая форма не отправляется')

  await fill(dlg, 'Название', 'My LLM')
  await fill(dlg, 'Адрес сервиса', 'http://api.example.com/v1')
  await dlg.getByText(/Обычный http допустим только для этого компьютера/).waitFor()
  await fill(dlg, 'Адрес сервиса', 'https://10.0.0.5/v1')
  await dlg.getByText(/Адрес похож на внутренний/).waitFor()
  await fill(dlg, 'Первая модель', 'bad name')
  await dlg.getByText(/Имя модели у провайдера: латинские буквы/).waitFor()
  await fill(dlg, 'Первая модель', 'my-model')
  assert.equal(await create.isDisabled(), false)

  // флажок «Адрес во внутренней сети»: сервер просит подтверждение, диалог рисков один и стоит над окном формы
  await dlg.getByLabel('Адрес во внутренней сети').check()
  await create.click()
  const d = page.getByRole('dialog', { name: /Новый провайдер «My LLM»/ })
  await d.waitFor()
  await d.getByText(/внутреннюю сеть/).first().waitFor()
  await d.getByRole('button', { name: /^Принимаю риски и применить/ }).click()
  await d.getByText('Готово', { exact: true }).waitFor()
  const cmd = dash.ops('ctl').find((m) => m.kind === 'command')
  assert.equal(cmd.action, 'llm.provider')
  assert.equal(cmd.args.op, 'add')
  assert.equal(cmd.args.id, 'my_llm', 'служебное имя построено из названия')
  assert.equal(cmd.args.spec.name, 'My LLM')
  assert.equal(cmd.args.spec.base_url, 'https://10.0.0.5/v1')
  assert.equal(cmd.args.spec.key_file, 'my_llm', 'файл ключа по умолчанию по имени провайдера')
  assert.equal(cmd.args.spec.allow_private, true)
  assert.deepEqual(Object.keys(cmd.args.spec.models), ['my-model'])
  assert.deepEqual(cmd.args.accepted, ['llm.allow_private'], 'принятое предупреждение уходит в команде')
  assert.equal(JSON.stringify(cmd).includes('"key"'), false, 'ключа в команде нет')
  await leaveDialog(page, d)
  await d.waitFor({ state: 'hidden' })

  // та же форма теперь в режиме «изменить» созданного провайдера: ключ и модели
  const edit = page.getByRole('dialog', { name: 'My LLM', exact: true })
  await edit.waitFor()
  await edit.getByText(/Провайдер создан/).waitFor()
  await edit.getByRole('heading', { name: 'Ключ доступа' }).waitFor()
  await edit.getByRole('heading', { name: 'Модели', exact: true }).waitFor()
  assert.equal(await edit.getByLabel('Тип').isDisabled(), true, 'тип после создания не меняется')
  assert.equal((await edit.getByLabel('Название').inputValue()), 'My LLM')
  const text = await edit.innerText()
  assert.ok(!EMOJI.test(text), 'эмодзи в окне')
  await shot(page, 'provider-created')
  await page.keyboard.press('Escape')
  await edit.waitFor({ state: 'hidden' })
  await page.getByText('My LLM', { exact: true }).first().waitFor()
})

scenario('Список моделей у провайдера: поиск, усечение словами, выбор, добавление модели с нулевой ценой, ключ не нужен в команде', async ({ page, open, dash }) => {
  installProviderRules(dash)
  await open(SETTINGS)
  const dlg = await openEdit(page, 'DeepSeek')
  await dlg.getByRole('heading', { name: 'Модели', exact: true }).waitFor()
  await dlg.getByRole('button', { name: 'Получить список моделей' }).click()
  await dlg.getByText(/Показаны первые 500 по алфавиту из 640/).waitFor()
  const list = dlg.getByRole('list', { name: 'Модели провайдера' })
  assert.equal(await list.getByRole('button').count(), 100, 'в списке не больше 100 строк')
  await dlg.getByText(/Показаны первые 100: уточните поиск/).waitFor()

  await dlg.getByLabel('Поиск по списку').fill('model-02')
  await dlg.getByText(/Подходит: 10\./).waitFor()
  await list.getByRole('button', { name: 'acme/model-023' }).click()
  assert.equal(await dlg.getByLabel('Имя модели у провайдера').inputValue(), 'acme/model-023')
  await dlg.getByRole('button', { name: 'Добавить', exact: true }).click()
  await waitFor(() => dash.ops('ctl').some((m) => m.kind === 'command' && m.args.op === 'model_set'), 'команда model_set')
  await dlg.getByText('Данные обновились.').first().waitFor()
  const cmd = dash.ops('ctl').filter((m) => m.kind === 'command').pop()
  assert.deepEqual(cmd.args.spec, { name: 'acme/model-023', tools: false, json_mode: false, price: { in_miss: 0, in_hit: 0, out: 0 } })
  assert.equal(cmd.args.model, 'acme-model-023')
  assert.equal(await dlg.getByLabel('Имя модели у провайдера').inputValue(), '', 'поле очищено после добавления')
  // повтор того же имени: нельзя
  await dlg.getByLabel('Имя модели у провайдера').fill('acme/model-023')
  await dlg.getByText(/уже добавлена/).first().waitFor()
  assert.equal(await dlg.getByRole('button', { name: 'Добавить', exact: true }).isDisabled(), true)
})

scenario('Проверка подключения: платный запрос назван, модель не выбирается, итог со временем и токенами', async ({ page, open, dash }) => {
  installProviderRules(dash)
  await open(SETTINGS)
  const dlg = await openEdit(page, 'DeepSeek')
  await dlg.getByRole('heading', { name: 'Проверка подключения' }).waitFor()
  await dlg.getByText(/Платный запрос/).first().waitFor()
  assert.equal(await dlg.getByText('Какую модель спросить').count(), 0, 'выбора модели для проверки нет')
  await dlg.getByRole('button', { name: 'Проверить подключение' }).click()
  await dlg.getByText(/Ответ за 0,84 с\. Модель deepseek-flash\. Токенов: вход 9, выход 2\./).waitFor()
  const cmd = dash.ops('ctl').filter((m) => m.kind === 'command').pop()
  assert.deepEqual(cmd.args, { op: 'ping', id: 'deepseek', accepted: [] })
})

scenario('Ошибки провайдера человеческими словами, без сырых кодов; без ключа сеть недоступна с пояснением', async ({ page, open, dash }) => {
  installProviderRules(dash, { network: { models: { error: 'auth', status: 401, detail: 'invalid api key' }, ping: { error: 'timeout', detail: 'timed out' } } })
  await open(SETTINGS)
  const dlg = await openEdit(page, 'DeepSeek')
  await dlg.getByRole('button', { name: 'Получить список моделей' }).click()
  await dlg.getByText(/Провайдер не принял ключ/).waitFor()
  await dlg.getByText(/Код ответа: 401/).waitFor()
  await dlg.getByText('Что ответил сервер').click()
  await dlg.getByText('invalid api key').waitFor()
  await dlg.getByRole('button', { name: 'Проверить подключение' }).click()
  await dlg.getByText(/Провайдер не ответил вовремя/).waitFor()
  const panelText = await dlg.innerText()
  assert.ok(!RAW.test(panelText.replace(/invalid api key/g, '')), `сырой код в тексте: ${panelText.match(RAW)?.[0]}`)
  await page.keyboard.press('Escape')
  await dlg.waitFor({ state: 'hidden' })

  // провайдер без ключа: кнопки закрыты, причина названа
  const anth = await openEdit(page, 'Anthropic')
  assert.equal(await anth.getByRole('button', { name: 'Получить список моделей' }).isDisabled(), true)
  assert.equal(await anth.getByRole('button', { name: 'Проверить подключение' }).isDisabled(), true)
  await anth.getByText(/Сначала задайте ключ провайдера/).waitFor()
  await anth.getByLabel('Ключ провайдера').waitFor()
})

scenario('Правка провайдера: адрес с предупреждением о ключе в диалоге, затем ограничения расхода одним сохранением', async ({ page, open, dash }) => {
  installProviderRules(dash)
  await open(SETTINGS)
  const dlg = await openEdit(page, 'DeepSeek')
  const save = dlg.getByRole('button', { name: 'Сохранить', exact: true })
  assert.equal(await save.isDisabled(), true, 'пока ничего не изменено, сохранять нечего')
  await dlg.getByLabel('Адрес сервиса').fill('https://new.example.com/v1')
  await dlg.getByText(/Ключ провайдера после смены будет уходить на новый адрес/).waitFor()
  await save.click()
  const d = page.getByRole('dialog', { name: 'Провайдер «DeepSeek»' })
  await d.waitFor()
  await d.getByText(/Ключ провайдера уйдёт на новый адрес/).waitFor()
  await d.getByRole('button', { name: /^Принимаю риски и применить/ }).click()
  await d.getByText('Готово', { exact: true }).waitFor()
  let cmd = dash.ops('ctl').filter((m) => m.kind === 'command').pop()
  assert.deepEqual(cmd.args.patch, { base_url: 'https://new.example.com/v1' })
  assert.deepEqual(cmd.args.accepted, ['llm.key_new_url'])
  await leaveDialog(page, d)
  await d.waitFor({ state: 'hidden' })
  await dlg.getByText(/ключ провайдера теперь уходит на new\.example\.com/).waitFor()
  await dlg.locator('input[placeholder*="new.example.com"]').waitFor()

  // ограничения расхода и название одним патчем, без диалога рисков
  await dlg.getByText(/Ограничения расхода/).click()
  await dlg.getByLabel('Деньги за день').fill('abc')
  await dlg.getByText(/Введите число/).waitFor()
  assert.equal(await save.isDisabled(), true, 'плохое число блокирует сохранение')
  await dlg.getByLabel('Деньги за день').fill('5')
  await dlg.getByLabel('Название').fill('DeepSeek Pro')
  await dlg.getByText(/Будет изменено: название: DeepSeek → DeepSeek Pro; деньги за день: \$3 → \$5/).waitFor()
  await save.click()
  await waitFor(() => dash.ops('ctl').filter((m) => m.kind === 'command').length >= 2, 'вторая команда update')
  cmd = dash.ops('ctl').filter((m) => m.kind === 'command').pop()
  assert.deepEqual(cmd.args.patch, { name: 'DeepSeek Pro', limits: { day_usd: 5, hour_usd: 1, day_tokens: 8000000, concurrent: 2 } })
  await dlg.getByRole('heading', { name: 'DeepSeek Pro', exact: true }).waitFor()
})

scenario('Адаптив 390 px: форма провайдера без горизонтальной прокрутки, подписи у всех полей', { viewport: { width: 390, height: 844 } }, async ({ page, open, dash }) => {
  installProviderRules(dash)
  await open(SETTINGS)
  await noOverflow(page, 'страница настроек, 390 px')
  await page.getByRole('button', { name: 'Добавить провайдера' }).click()
  const dlg = page.getByRole('dialog', { name: 'Новый провайдер', exact: true })
  await dlg.waitFor()
  await fill(dlg, 'Адрес сервиса', 'https://api.example.com/v1/with/a/very/long/path/that/keeps/going/and/going/and/going/forever')
  await noOverflow(page, 'форма, 390 px')
  const box = await dlg.boundingBox()
  assert.ok(box.x >= 0 && box.x + box.width <= 390, `окно помещается в экран: ${JSON.stringify(box)}`)
  await shot(page, 'provider-form-390')
  const unlabeled = await dlg.evaluate((el) => [...el.querySelectorAll('input:not([type=hidden]), select')].filter((n) => !n.labels?.length && !n.getAttribute('aria-label')).map((n) => n.id || n.name))
  assert.deepEqual(unlabeled, [], 'у каждого поля есть подпись')
  await page.keyboard.press('Escape')
  await dlg.waitFor({ state: 'hidden' })
  const edit = await openEdit(page, 'DeepSeek')
  await edit.getByRole('button', { name: 'Получить список моделей' }).click()
  await edit.getByText(/Показаны первые 500/).waitFor()
  await noOverflow(page, 'форма изменения, 390 px')
  await shot(page, 'provider-edit-390')
})
