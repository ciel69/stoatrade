import { describe, expect, it } from 'vitest'
import { addArgs, addErrors, addressPatch, addressReflects, emptyAdd, envName, filterIds, looksPrivate, modelAddReflects, modelKey, modelsNote, modelsOutcome, netErrorText, netReady, pingLine, pingOutcome, urlProblem } from './providerNet'
import type { ProviderRow } from '../model/types'

const ok = { ...emptyAdd(), id: 'mine', baseUrl: 'https://api.example.com/v1/', model: 'gpt-4o-mini' }

describe('адрес провайдера', () => {
  it('https, localhost по http; остальное отвергается словами', () => {
    expect(urlProblem('https://api.example.com/v1')).toBeNull()
    expect(urlProblem('http://localhost:11434/v1')).toBeNull()
    expect(urlProblem('http://127.0.0.1:8000')).toBeNull()
    expect(urlProblem('')).toMatch(/Укажите/)
    expect(urlProblem('ftp://x.example')).toMatch(/https/)
    expect(urlProblem('api.example.com')).toMatch(/https/)
    expect(urlProblem('http://api.example.com')).toMatch(/только для этого компьютера/)
    expect(urlProblem('https://user:pw@api.example.com')).toMatch(/логина/)
    expect(urlProblem('https://api.example.com/v1?key=1')).toMatch(/параметров/)
    expect(urlProblem('https://api.example.com/v1#x')).toMatch(/фрагмента/)
    expect(urlProblem('https://api. example.com')).toMatch(/пробел/)
  })
  it('внутренние адреса узнаются, внешние и localhost нет', () => {
    for (const u of ['https://10.0.0.5/v1', 'https://192.168.1.2', 'https://172.16.0.1', 'https://172.31.9.9', 'https://llm.lan', 'https://gpu-box/v1', 'https://x.internal']) expect(looksPrivate(u), u).toBe(true)
    for (const u of ['https://api.example.com', 'https://172.32.0.1', 'http://localhost:11434', 'not a url']) expect(looksPrivate(u), u).toBe(false)
  })
})

describe('форма «Свой провайдер»', () => {
  it('ошибки: имя, адрес, файл ключа, модель; занятое имя', () => {
    expect(Object.keys(addErrors(emptyAdd(), []))).toEqual(['id', 'baseUrl', 'model'])
    expect(addErrors({ ...ok, id: 'deepseek' }, ['deepseek']).id).toMatch(/уже есть/)
    expect(addErrors({ ...ok, keyFile: '../x' }, []).keyFile).toBeTruthy()
    expect(addErrors({ ...ok, keyFile: 'a b' }, []).keyFile).toBeTruthy()
    expect(addErrors({ ...ok, model: 'bad model' }, []).model).toBeTruthy()
    expect(addErrors(ok, [])).toEqual({})
  })
  it('аргументы add: spec с адресом без хвостового слэша, файлом ключа по id и первой моделью с нулевой ценой', () => {
    expect(addArgs(ok, [])).toEqual({
      op: 'add', id: 'mine',
      spec: { name: 'mine', adapter: 'openai_compat', base_url: 'https://api.example.com/v1', key_file: 'mine', models: { 'gpt-4o-mini': { name: 'gpt-4o-mini', tools: false, json_mode: false, price: { in_miss: 0, in_hit: 0, out: 0 } } } },
    })
    expect(addArgs({ ...emptyAdd() }, [])).toBeNull()
  })
  it('для Anthropic и DeepSeek добавляется имя переменной ключа; allow_private только когда включён', () => {
    const a = addArgs({ ...ok, adapter: 'anthropic', keyFile: 'my.key', allowPrivate: true }, [])
    expect(a?.spec).toMatchObject({ adapter: 'anthropic', key_file: 'my.key', key_env: 'MINE_API_KEY', allow_private: true })
    expect(addArgs(ok, [])?.spec).not.toHaveProperty('allow_private')
    expect(addArgs(ok, [])?.spec).not.toHaveProperty('key_env')
    expect(envName('1my-llm')).toBe('LLM_1MY_LLM_API_KEY')
  })
  it('ключ модели из имени: безопасные знаки, длина, обход занятых', () => {
    expect(modelKey('openai/gpt-4o:free', [])).toBe('openai-gpt-4o-free')
    expect(modelKey('GPT_5', ['gpt_5'])).toBe('gpt_5-2')
    expect(modelKey('x'.repeat(50), [])).toHaveLength(30)
    expect(modelKey('///', [])).toBe('model')
    expect(modelKey('a', ['a', 'a-2'])).toBe('a-3')
  })
})

describe('смена адреса', () => {
  it('патч только из изменившегося; пустое поле адреса не меняет адрес; ошибка адреса даёт null', () => {
    const was = { allow_private: false }
    expect(addressPatch({ baseUrl: '', allowPrivate: false }, was)).toBeNull()
    expect(addressPatch({ baseUrl: 'https://new.example.com/v1/', allowPrivate: false }, was)).toEqual({ base_url: 'https://new.example.com/v1' })
    expect(addressPatch({ baseUrl: '', allowPrivate: true }, was)).toEqual({ allow_private: true })
    expect(addressPatch({ baseUrl: 'http://bad.example.com', allowPrivate: false }, was)).toBeNull()
    expect(addressPatch({ baseUrl: '', allowPrivate: false }, {})).toBeNull()
  })
})

describe('отражение смены адреса', () => {
  it('хост и флаг совпали', () => {
    const p = { base_host: 'new.example.com', allow_private: true }
    expect(addressReflects(p, { base_url: 'https://new.example.com/v1', allow_private: true })).toBe(true)
    expect(addressReflects(p, { base_url: 'https://old.example.com' })).toBe(false)
    expect(addressReflects(p, { allow_private: false })).toBe(false)
    expect(addressReflects(null, {})).toBe(false)
  })
})

describe('итоги сети словами', () => {
  const done = (data: Record<string, unknown>) => ({ status: 'done', code: 'OK', msg: '', data })
  it('список моделей: успех и усечение', () => {
    const r = modelsOutcome(done({ ok: true, ids: ['a', 'b'], count: 640, truncated: true }), 'x')
    expect(r).toEqual({ ok: true, ids: ['a', 'b'], count: 640, truncated: true })
    if (r.ok) expect(modelsNote(r, 2, 2, '')).toMatch(/первые 2 по алфавиту из 640/)
  })
  it('ошибки провайдера: слова по коду, код ответа, чужой текст коротко', () => {
    const r = modelsOutcome(done({ ok: false, error: 'auth', status: 401, detail: 'bad\n   key ' + 'x'.repeat(400) }), 'x')
    expect(r.ok).toBe(false)
    if (!r.ok) {
      expect(r.text).toMatch(/не принял ключ/)
      expect(r.text).toMatch(/401/)
      expect(r.detail.length).toBeLessThanOrEqual(200)
      expect(r.detail).not.toMatch(/\n/)
    }
    for (const c of ['auth', 'rate', 'server', 'timeout', 'network', 'redirect', 'bad_body', 'config', 'billing', 'bad_request', 'zzz']) expect(netErrorText(c)).not.toMatch(/[a-z]_[a-z]|undefined/)
  })
  it('отказ команды: лимит частоты называет секунды, нет ключа, прочее текстом платформы', () => {
    const rate = modelsOutcome({ status: 'rejected', code: 'E_RATE', msg: '', data: { retry_after_s: 1.2 } }, 'x')
    expect(rate.ok === false && rate.retryS).toBe(2)
    expect(rate.ok === false && rate.text).toMatch(/через 2 с/)
    const nokey = pingOutcome({ status: 'rejected', code: 'E_NO_KEY', msg: '' }, 'x')
    expect(nokey.ok === false && nokey.text).toMatch(/нет ключа/)
    const other = pingOutcome({ status: 'rejected', code: 'E_ARGS', msg: 'm' }, 'Платформа не поняла')
    expect(other.ok === false && other.text).toBe('Платформа не поняла')
    expect(modelsOutcome(undefined, 'Нет связи').ok).toBe(false)
  })
  it('проверка подключения: строка с временем, моделью и токенами', () => {
    const r = pingOutcome(done({ ok: true, ms: 840, model: 'm', model_name: 'gpt-4o-mini', paid: true, usage: { in: 9, out: 2 }, text: 'pong' }), 'x')
    expect(r.ok).toBe(true)
    if (r.ok) expect(pingLine(r)).toBe('Ответ за 0,84 с. Модель gpt-4o-mini. Токенов: вход 9, выход 2.')
    const bad = pingOutcome(done({ ok: false, error: 'timeout', model: 'm', paid: true }), 'x')
    expect(bad.ok === false && bad.text).toMatch(/не ответил/)
  })
})

describe('список моделей и готовность', () => {
  it('фильтр по подстроке без регистра с лимитом показа', () => {
    const ids = Array.from({ length: 300 }, (_, i) => `Acme/m-${i}`)
    expect(filterIds(ids, '').shown).toHaveLength(100)
    expect(filterIds(ids, '').total).toBe(300)
    expect(filterIds(ids, 'ACME/M-29')).toEqual({ shown: ['Acme/m-29', 'Acme/m-290', 'Acme/m-291', 'Acme/m-292', 'Acme/m-293', 'Acme/m-294', 'Acme/m-295', 'Acme/m-296', 'Acme/m-297', 'Acme/m-298', 'Acme/m-299'], total: 11 })
    expect(filterIds(ids, 'nope').total).toBe(0)
  })
  it('сеть доступна, когда ключ задан или не нужен', () => {
    const p = (o: Partial<ProviderRow>) => ({ local: false, cred: { needed: true, present: false, perms_ok: null, env: null }, ...o }) as Pick<ProviderRow, 'local' | 'cred'>
    expect(netReady(p({}), false)).toBe(false)
    expect(netReady(p({}), true)).toBe(true)
    expect(netReady(p({ local: true }), false)).toBe(true)
    expect(netReady(p({ cred: { needed: false, present: false, perms_ok: null, env: null } }), false)).toBe(true)
    expect(netReady(null, true)).toBe(false)
  })
  it('модель отражена, когда у провайдера есть модель с таким именем', () => {
    expect(modelAddReflects({ models: [{ id: 'k', name: 'gpt-x', ctx: null, tools: false, tags: [], price: null }] }, ' gpt-x ')).toBe(true)
    expect(modelAddReflects(null, 'x')).toBe(false)
  })
})
