/**
 * Сценарий 12. Свой провайдер LLM: форма «Свой провайдер» в «Для специалиста», единый диалог «Принимаю риски» для внутренней сети, список моделей у провайдера,
 * добавление модели, проверка подключения (платный запрос), смена адреса с предупреждением о ключе, ошибки словами, адаптив 390 px.
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
        case 'update': return done({ send_warning: { code: 'KEY_NEW_URL', host: 'new.example.com', msg: 'ключ провайдера теперь уходит на new.example.com' } }, () => d.patch('settings', (s) => mock.mockAddress(s, args.id, args.patch)))
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

const dlg = (page) => page.getByRole('dialog')
const expert = async (page) => {
  await page.getByText('Для специалиста: профили моделей и новые провайдеры').click()
  await page.getByRole('heading', { name: 'Добавить провайдера', level: 3 }).waitFor()
}
const fill = async (page, label, value) => { const f = page.getByLabel(label, { exact: false }); await f.fill(value) }

/** Снимок страницы в папку из E2E_SHOTS (для просмотра глазами; без переменной ничего не делает). */
async function shot(page, name) {
  const dir = process.env.E2E_SHOTS
  if (dir) await page.screenshot({ path: path.join(dir, `${name}.png`), fullPage: true }).catch(() => {})
}

async function noOverflow(page, what) {
  const m = await page.evaluate(() => ({ sw: document.scrollingElement.scrollWidth, cw: document.scrollingElement.clientWidth }))
  assert.ok(m.sw <= m.cw, `${what}: горизонтальная прокрутка ${m.sw} px при окне ${m.cw} px`)
}

scenario('Свой провайдер: проверка полей словами, внутренняя сеть через единый диалог, провайдер появляется в списке', async ({ page, open, dash }) => {
  installProviderRules(dash)
  await open(SETTINGS)
  await expert(page)
  await page.getByRole('button', { name: 'Свой провайдер' }).click()
  const add = page.getByRole('button', { name: 'Добавить провайдера', exact: true })
  await add.waitFor()
  assert.equal(await add.isDisabled(), true, 'пустая форма не отправляется')

  await fill(page, 'Короткое имя', 'Bad Name')
  await page.getByText(/строчные латинские буквы/).waitFor()
  await fill(page, 'Короткое имя', 'my_llm')
  await fill(page, 'Адрес REST', 'http://api.example.com/v1')
  await page.getByText(/Обычный http допустим только для этого компьютера/).waitFor()
  await fill(page, 'Адрес REST', 'https://10.0.0.5/v1')
  await page.getByText(/Адрес похож на внутренний/).waitFor()
  await fill(page, 'Модель у провайдера', 'my-model')
  assert.equal(await add.isDisabled(), false)

  // флажок «Адрес во внутренней сети»: сервер просит подтверждение, диалог один
  await page.getByLabel('Адрес во внутренней сети').check()
  await add.click()
  const d = page.getByRole('dialog', { name: /Добавить провайдера/ })
  await d.waitFor()
  await d.getByText(/внутреннюю сеть/).first().waitFor()
  await d.getByRole('button', { name: /^Принимаю риски и применить/ }).click()
  await d.getByText('Готово', { exact: true }).waitFor()
  const cmd = dash.ops('ctl').find((m) => m.kind === 'command')
  assert.equal(cmd.action, 'llm.provider')
  assert.equal(cmd.args.op, 'add')
  assert.equal(cmd.args.id, 'my_llm')
  assert.equal(cmd.args.spec.base_url, 'https://10.0.0.5/v1')
  assert.equal(cmd.args.spec.key_file, 'my_llm', 'файл ключа по умолчанию по имени провайдера')
  assert.equal(cmd.args.spec.allow_private, true)
  assert.deepEqual(Object.keys(cmd.args.spec.models), ['my-model'])
  assert.deepEqual(cmd.args.accepted, ['llm.allow_private'], 'принятое предупреждение уходит в команде')
  assert.equal(JSON.stringify(cmd).includes('"key"'), false, 'ключа в команде нет')
  await leaveDialog(page, d)
  await d.waitFor({ state: 'hidden' })

  await page.getByText(/Провайдер «my_llm» добавлен/).waitFor()
  const text = await page.locator('main').innerText()
  assert.ok(!EMOJI.test(text), 'эмодзи на странице')
  await page.getByRole('button', { name: 'Открыть провайдера' }).click()
  await page.waitForURL(/provider=my_llm/)
  await page.getByRole('heading', { name: 'Ключ доступа' }).waitFor()
})

scenario('Список моделей у провайдера: поиск, усечение словами, выбор, добавление модели с нулевой ценой, ключ не нужен в команде', async ({ page, open, dash }) => {
  installProviderRules(dash)
  await open(`${SETTINGS}?provider=deepseek`)
  await page.getByRole('heading', { name: 'Модели у провайдера' }).waitFor()
  await page.getByRole('button', { name: 'Получить список' }).click()
  await page.getByText(/Показаны первые 500 по алфавиту из 640/).waitFor()
  const list = page.getByRole('list', { name: 'Модели провайдера' })
  assert.equal(await list.getByRole('button').count(), 100, 'в списке не больше 100 строк')
  await page.getByText(/Показаны первые 100: уточните поиск/).waitFor()

  await page.getByLabel('Поиск по списку').fill('model-02')
  await page.getByText(/Подходит: 10\./).waitFor()
  await list.getByRole('button', { name: 'acme/model-023' }).click()
  assert.equal(await page.getByLabel('Имя модели у провайдера').inputValue(), 'acme/model-023')
  await page.getByRole('button', { name: 'Добавить модель' }).click()
  await waitFor(() => dash.ops('ctl').some((m) => m.kind === 'command' && m.args.op === 'model_set'), 'команда model_set')
  await page.getByText('Данные обновились.').first().waitFor()
  const cmd = dash.ops('ctl').filter((m) => m.kind === 'command').pop()
  assert.deepEqual(cmd.args.spec, { name: 'acme/model-023', tools: false, json_mode: false, price: { in_miss: 0, in_hit: 0, out: 0 } })
  assert.equal(cmd.args.model, 'acme-model-023')
  assert.equal(await page.getByLabel('Имя модели у провайдера').inputValue(), '', 'поле очищено после добавления')
  // повтор того же имени: нельзя
  await page.getByLabel('Имя модели у провайдера').fill('acme/model-023')
  await page.getByText(/уже добавлена/).first().waitFor()
  assert.equal(await page.getByRole('button', { name: 'Добавить модель' }).isDisabled(), true)
})

scenario('Проверка подключения: платный запрос назван, итог со временем и токенами', async ({ page, open, dash }) => {
  installProviderRules(dash)
  await open(`${SETTINGS}?provider=deepseek`)
  await page.getByRole('heading', { name: 'Проверка подключения' }).waitFor()
  await page.getByText(/Платный запрос/).first().waitFor()
  await page.getByRole('button', { name: 'Проверить подключение' }).click()
  await page.getByText(/Ответ за 0,84 с\. Модель deepseek-flash\. Токенов: вход 9, выход 2\./).waitFor()
  const cmd = dash.ops('ctl').filter((m) => m.kind === 'command').pop()
  assert.deepEqual(cmd.args, { op: 'ping', id: 'deepseek', accepted: [] })
})

scenario('Ошибки провайдера человеческими словами, без сырых кодов; без ключа сеть недоступна с пояснением', async ({ page, open, dash }) => {
  installProviderRules(dash, { network: { models: { error: 'auth', status: 401, detail: 'invalid api key' }, ping: { error: 'timeout', detail: 'timed out' } } })
  await open(`${SETTINGS}?provider=deepseek`)
  await page.getByRole('button', { name: 'Получить список' }).click()
  await page.getByText(/Провайдер не принял ключ/).waitFor()
  await page.getByText(/Код ответа: 401/).waitFor()
  await page.getByText('Что ответил сервер').click()
  await page.getByText('invalid api key').waitFor()
  await page.getByRole('button', { name: 'Проверить подключение' }).click()
  await page.getByText(/Провайдер не ответил вовремя/).waitFor()
  const panelText = await page.locator('aside, [role="complementary"], main').last().innerText()
  assert.ok(!RAW.test(panelText.replace(/invalid api key/g, '')), `сырой код в тексте: ${panelText.match(RAW)?.[0]}`)

  // провайдер без ключа: кнопки закрыты, причина названа
  await page.goto(page.url().replace('provider=deepseek', 'provider=anthropic'))
  await page.getByRole('heading', { name: 'Модели у провайдера' }).waitFor()
  assert.equal(await page.getByRole('button', { name: 'Получить список' }).isDisabled(), true)
  assert.equal(await page.getByRole('button', { name: 'Проверить подключение' }).isDisabled(), true)
  await page.getByText(/Сначала задайте ключ провайдера/).waitFor()
})

scenario('Смена адреса: предупреждение о ключе до отправки и в диалоге, принятое предупреждение уходит в команде', async ({ page, open, dash }) => {
  installProviderRules(dash)
  await open(`${SETTINGS}?provider=deepseek`)
  await page.getByRole('heading', { name: 'Адрес', level: 4 }).waitFor()
  const save = page.getByRole('button', { name: 'Сохранить адрес' })
  assert.equal(await save.isDisabled(), true, 'пока ничего не изменено, сохранять нечего')
  await page.getByLabel('Новый адрес REST').fill('https://new.example.com/v1')
  await page.getByText(/Ключ провайдера после смены будет уходить на новый адрес/).waitFor()
  await save.click()
  const d = page.getByRole('dialog', { name: 'Провайдер «DeepSeek»' })
  await d.waitFor()
  await d.getByText(/Ключ провайдера уйдёт на новый адрес/).waitFor()
  await d.getByRole('button', { name: /^Принимаю риски и применить/ }).click()
  await d.getByText('Готово', { exact: true }).waitFor()
  const cmd = dash.ops('ctl').filter((m) => m.kind === 'command').pop()
  assert.deepEqual(cmd.args.patch, { base_url: 'https://new.example.com/v1' })
  assert.deepEqual(cmd.args.accepted, ['llm.key_new_url'])
  await leaveDialog(page, d)
  await page.getByText(/ключ провайдера теперь уходит на new\.example\.com/).waitFor()
  await page.getByText(/Сейчас: new\.example\.com/).waitFor()
})

scenario('Адаптив 390 px: форма провайдера и панель моделей без горизонтальной прокрутки, подписи у всех полей', { viewport: { width: 390, height: 844 } }, async ({ page, open, dash }) => {
  installProviderRules(dash)
  await open(SETTINGS)
  await expert(page)
  await page.getByRole('button', { name: 'Свой провайдер' }).click()
  await page.getByLabel('Адрес REST').fill('https://api.example.com/v1/with/a/very/long/path/that/keeps/going/and/going/and/going/forever')
  await noOverflow(page, 'форма, 390 px')
  await shot(page, 'provider-form-390')
  const unlabeled = await page.evaluate(() => [...document.querySelectorAll('form input:not([type=hidden]), form select')].filter((n) => !n.labels?.length && !n.getAttribute('aria-label')).map((n) => n.id || n.name))
  assert.deepEqual(unlabeled, [], 'у каждого поля есть подпись')
  await page.goto(page.url().split('?')[0] + '?provider=deepseek')
  await page.getByRole('button', { name: 'Получить список' }).click()
  await page.getByText(/Показаны первые 500/).waitFor()
  await noOverflow(page, 'панель провайдера, 390 px')
  await shot(page, 'provider-panel-390')
})
