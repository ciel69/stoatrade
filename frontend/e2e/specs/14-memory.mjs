/**
 * Сценарий 14. Вкладка «Мозг · Память» (/brain/memory, спека R2, задача 19; Requirements 13.1, 13.5, 13.7).
 * Фейковый сервер отвечает на memory_cases|memory_lessons|memory_rules|memory_path и на аварийные действия по протоколу memory_ops (CSRF из hello.memory,
 * challenge -> CONFIRM, команда только с accepted = все id). Проверки: блоки и числа с n, состояния без данных, независимость блоков,
 * действия (урок без диалога, правило и «все правила» через единое окно «Принимаю риски»), axe-core, reduced-motion, клавиатура, 390 px.
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { scenario, waitFor } from '../lib/harness.mjs'
import { T } from '../lib/env.mjs'
import { MEMORY_HELLO, NO_DATA, serveActions, serveMemory } from '../lib/memory-fixtures.mjs'

const AXE = readFileSync(createRequire(import.meta.url).resolve('axe-core/axe.min.js'), 'utf8')
const OPTS = { memory: MEMORY_HELLO }
const OPS = ['memory_cases', 'memory_lessons', 'memory_rules', 'memory_path']
const RAW = /\b(no_data|restoring|READY|MAX_ACTIVE|LOSS_CAP|OWNER_DISABLE|PROMOTE|ROLLBACK|NOT_SIGNIFICANT|E_[A-Z]+|rulebook|undefined|NaN|null|\[object)\b|\.\/tb/
const EMOJI = /[\p{Emoji_Presentation}\p{Extended_Pictographic}]/u

const main = (page) => page.locator('main')
const block = (page, name) => page.getByRole('region', { name })
const ready = async (page) => { await block(page, 'Правила сейчас').getByText('Действуют').waitFor() }
const settle = (page) => page.evaluate(() => Promise.all(document.getAnimations().filter((a) => a.effect?.getComputedTiming().iterations !== Infinity).map((a) => a.finished.catch(() => {}))))
const dialog = (page) => page.getByRole('dialog')

async function axe(page, what) {
  await page.addScriptTag({ content: AXE })
  const res = await page.evaluate(async () => {
    const r = await window.axe.run({ include: [['main'], ['[role="dialog"]']] }, { resultTypes: ['violations'] })
    return r.violations.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.slice(0, 3).map((n) => n.target.join(' ')) }))
  })
  const bad = res.filter((v) => v.impact === 'serious' || v.impact === 'critical')
  assert.deepEqual(bad, [], `${what}: axe-core нашёл серьёзные нарушения: ${JSON.stringify(bad)}`)
}

/** Максимальные вычисленные длительности (мс) переходов и анимаций на вкладке и в окне подтверждения. */
const maxDurations = (page) => page.evaluate(() => {
  const ms = (s) => Math.max(0, ...s.split(',').map((x) => (x.trim().endsWith('ms') ? parseFloat(x) : parseFloat(x) * 1000)).filter(Number.isFinite))
  const els = [...document.querySelectorAll('main, main *, [role="dialog"], [role="dialog"] *, .backdrop, .tip-pop')]
  let tr = 0; let an = 0; let worst = ''
  for (const e of els) {
    for (const pseudo of [null, '::before', '::after']) {
      const cs = getComputedStyle(e, pseudo)
      const t = ms(cs.transitionDuration) + ms(cs.transitionDelay)
      const a = cs.animationName !== 'none' ? ms(cs.animationDuration) + ms(cs.animationDelay) : 0
      if (t > tr) { tr = t; worst = `${e.tagName.toLowerCase()}.${String(e.className).split(' ')[0]}${pseudo ?? ''} transition ${t}` }
      if (a > an) { an = a; worst = `${e.tagName.toLowerCase()}.${String(e.className).split(' ')[0]}${pseudo ?? ''} animation ${a}` }
    }
  }
  // идущие анимации и переходы вкладки и окна (шапка и боковая колонка дашборда к вкладке не относятся)
  const own = (a) => { const t = a.effect?.target; return !!t && !!t.closest?.('main, [role="dialog"], .backdrop, .tip-pop') }
  const live = document.getAnimations().filter(own).map((x) => { const t = x.effect.getComputedTiming(); return { t: t.iterations === Infinity ? Infinity : t.endTime, el: `${x.effect.target.tagName}.${x.effect.target.className}` } })
  const top = live.sort((a, b) => b.t - a.t)[0]
  return { tr, an, live: top?.t ?? 0, liveEl: top?.el ?? '', worst }
})

// ---- вкладка, блоки, числа с n

scenario('Память: вкладка в разделе «Мозг», один заголовок, четыре запроса, все блоки на месте', OPTS, async ({ page, open, dash }) => {
  serveMemory(dash)
  await open('/brain/decisions')
  const tab = page.getByRole('link', { name: 'Память', exact: true })
  await tab.click()
  await page.waitForURL(/\/brain\/memory$/)
  assert.equal(await tab.getAttribute('aria-current'), 'page')
  await ready(page)
  assert.equal(await page.getByRole('heading', { level: 1 }).count(), 1)
  assert.equal(await page.getByRole('heading', { level: 1 }).textContent(), 'Память')
  for (const op of OPS) assert.ok(dash.ops(op).length >= 1, `запрос ${op}`)
  for (const name of ['Как наблюдение становится правилом', 'Правила сейчас', 'Уроки', 'Случаи']) await block(page, new RegExp(`^${name}`)).first().waitFor()
  const text = await main(page).innerText()
  assert.ok(!RAW.test(text), `сырой код в тексте: ${text.match(RAW)?.[0]}`)
  assert.ok(!EMOJI.test(text), 'эмодзи в тексте')
  assert.match(text, /Появится на этапе 3/, 'поиск похожих — заглушка этапа 3')
  assert.match(text, /Правила применяются/)
  assert.match(text, /версия\s*RV3/)
})

scenario('Числа с n: у урока эффект с интервалом и n, n < 30 помечено; доли проверок с n', OPTS, async ({ page, open, dash }) => {
  serveMemory(dash)
  await open('/brain/memory')
  await ready(page)
  const lessons = block(page, /^Уроки/)
  const t = await lessons.innerText()
  assert.match(t, /−0\.41 R/, 'эффект урока')
  assert.match(t, /от −0\.66 R до −0\.16 R/, 'интервал')
  assert.match(t, /n = 64/)
  assert.match(t, /n = 12[\s\S]*мало данных \(n < 30\)/, 'урок с n = 12 помечен')
  assert.match(t, /из разметки/)
  assert.match(t, /гипотеза/)
  assert.match(t, /без срока/)
  // «Все» показывает отклонённые
  await lessons.getByRole('button', { name: /^Все/ }).click()
  assert.match(await lessons.innerText(), /отклонён/)
  const rules = await block(page, 'Правила сейчас').innerText()
  assert.match(rules, /4%[\s\S]*n = 180/, 'доля проверок без данных с n')
  assert.match(rules, /25%[\s\S]*n = 24[\s\S]*мало данных/, 'доля решений без события с n и пометкой')
  assert.match(rules, /Больше 20%/, 'превышение порога названо словами')
})

scenario('Ожидающие правила: причина READY / MAX_ACTIVE / LOSS_CAP словами и срок', OPTS, async ({ page, open, dash }) => {
  serveMemory(dash)
  await open('/brain/memory')
  await ready(page)
  const t = await block(page, 'Правила сейчас').innerText()
  assert.match(t, /готово к включению/)
  assert.match(t, /ждёт места/)
  assert.match(t, /пауза после потерь[\s\S]*осталось 2 дня/)
  assert.match(t, /Не входить, когда прибыль к риску в плане ≤ 1\.5/)
})

scenario('Путь: воронка по шагам и путь кандидата до версии; отсеянный кандидат с причиной', OPTS, async ({ page, open, dash }) => {
  serveMemory(dash)
  await open('/brain/memory')
  await ready(page)
  const p = block(page, 'Как наблюдение становится правилом')
  const funnel = p.getByRole('list', { name: /Воронка/ })
  assert.match(await funnel.innerText(), /9[\s\S]*Кандидаты[\s\S]*3[\s\S]*Уроки[\s\S]*2[\s\S]*Испытания[\s\S]*1[\s\S]*Подтверждены[\s\S]*1[\s\S]*Правила действуют[\s\S]*RV3/)
  await p.getByRole('list', { name: /Кандидат: проверен; Урок: подтверждён; Испытание: пройдено; Правило: действует; Версия: в версии RV3/ }).waitFor()
  assert.match(await p.innerText(), /отсеян: разница оказалась случайной/)
})

// ---- состояния и независимость блоков

scenario('Нет таблиц цикла: каждый блок называет причину, аварийная кнопка доступна, страница жива', OPTS, async ({ page, open, dash }) => {
  serveMemory(dash, NO_DATA)
  await open('/brain/memory')
  await page.getByText(/Данных пока нет: уроков пока нет/).waitFor()
  const t = await main(page).innerText()
  assert.match(t, /Данных пока нет: случаев пока нет/)
  assert.match(t, /Правила не применяются: базы правил нет/)
  assert.match(t, /Данных пока нет: таблиц цикла нет/)
  assert.ok(!RAW.test(t), `сырой код: ${t.match(RAW)?.[0]}`)
  assert.ok(await page.getByRole('button', { name: 'Отключить все правила' }).isEnabled())
  assert.ok(await page.locator('aside[aria-label="Разделы"]').isVisible())
})

scenario('База восстанавливается и ошибка чтения: причина только в своём блоке, остальные с данными', OPTS, async ({ page, open, dash }) => {
  serveMemory(dash, {
    memory_rules: { ok: true, state: 'restoring', reason: 'база восстанавливается', mode: { apply: false, why: 'RESTORING', ver: null } },
    memory_path: { ok: false, state: 'error', reason: 'не удалось собрать', error: 'failed' },
  })
  await open('/brain/memory')
  await block(page, /^Уроки/).getByText('n = 64').waitFor()
  assert.match(await block(page, 'Правила сейчас').innerText(), /восстанавливается из резервной копии/)
  assert.match(await block(page, 'Как наблюдение становится правилом').innerText(), /Не удалось прочитать данные о пути уроков/)
  assert.match(await block(page, /^Случаи/).innerText(), /SOLUSDT/)
})

scenario('Ответ неизвестной формы: блок показывает ошибку, дашборд не ломается, в консоли нет ошибок', { ...OPTS, allowConsole: [/Блок вкладки/] }, async ({ page, open, dash }) => {
  serveMemory(dash, { memory_cases: 'garbage', memory_lessons: () => ({ ok: true, state: 'ok', rows: [{ lesson_id: 7, effect: 'x', claim: null }] }) })
  await open('/brain/memory')
  await ready(page)
  await block(page, /^Случаи/).getByText(/Не удалось прочитать данные о случаях/).waitFor()
  assert.ok(await page.locator('aside[aria-label="Разделы"]').isVisible())
  await page.getByRole('link', { name: 'Решения', exact: true }).click()
  await page.waitForURL(/\/brain\/decisions$/)
})

scenario('Нет связи: блоки говорят об этом словами; после возвращения связи данные запрашиваются снова', { ...OPTS, timeout: 60_000 }, async ({ page, open, dash }) => {
  serveMemory(dash)
  await open('/brain/memory')
  await ready(page)
  const before = dash.ops('memory_rules').length
  dash.blockConnections = true
  dash.dropAll()
  await page.waitForTimeout(200)
  dash.blockConnections = false
  await waitFor(() => dash.ops('memory_rules').length > before, 'повторный запрос после переподключения', T.reconnect)
})

// ---- аварийные действия

scenario('Отклонить урок: без диалога, команда с CSRF из hello.memory, уведомление, блоки обновляются', OPTS, async ({ page, open, dash }) => {
  serveMemory(dash)
  serveActions(dash)
  await open('/brain/memory')
  await ready(page)
  const before = dash.ops('memory_lessons').length
  await page.getByRole('button', { name: 'Отклонить урок L-0004' }).click()
  await page.getByRole('status').filter({ hasText: 'Урок отклонён' }).waitFor()
  assert.equal(await dialog(page).count(), 0, 'окна подтверждения нет')
  const sent = dash.ops('lesson_reject')
  assert.equal(sent.length, 1, 'одна команда, без challenge')
  assert.deepEqual({ kind: sent[0].kind, csrf: sent[0].csrf, lesson_id: sent[0].lesson_id }, { kind: 'command', csrf: MEMORY_HELLO.csrf, lesson_id: 'L-0004' })
  await waitFor(() => dash.ops('memory_lessons').length > before, 'уроки перезапрошены')
  assert.equal(await page.getByRole('button', { name: 'Отклонить урок L-0002' }).count(), 0, 'у урока мозга кнопки нет')
})

scenario('Отключить правило: единое окно «Принимаю риски» с последствиями словами, команда с accepted = все id', OPTS, async ({ page, open, dash }) => {
  serveMemory(dash)
  serveActions(dash)
  await open('/brain/memory')
  await ready(page)
  await page.getByRole('button', { name: 'Отключить правило RL0001' }).click()
  const d = dialog(page)
  await d.getByRole('heading', { name: 'Отключить правило RL0001' }).waitFor()
  const t = await d.innerText()
  assert.match(t, /Что нужно принять/)
  assert.match(t, /Правило перестанет действовать сразу/)
  assert.match(t, /никогда не предложит это правило сама/)
  assert.ok(!/\.\/tb|rulebook/.test(t), 'команд терминала в окне нет')
  assert.equal(dash.ops('rule_disable').filter((m) => m.kind === 'command').length, 0, 'до подтверждения команды нет')
  await d.getByRole('button', { name: 'Принимаю риски и применить' }).click()
  await d.getByText('Готово').waitFor()
  const cmd = dash.ops('rule_disable').find((m) => m.kind === 'command')
  assert.deepEqual([...cmd.accepted].sort(), ['block_forever', 'rule_off'])
  assert.equal(cmd.rule_id, 'RL0001')
  assert.equal(cmd.csrf, MEMORY_HELLO.csrf)
})

scenario('«Отключить все правила» с клавиатуры: Tab до кнопки, Enter, Esc отменяет без команды, затем подтверждение', OPTS, async ({ page, open, dash }) => {
  serveMemory(dash)
  serveActions(dash, { result: { rules_disable_all: { ok: false, code: 'E_RETRY', msg: 'выключатель записан' } } })
  await open('/brain/memory')
  await ready(page)
  const btn = page.getByRole('button', { name: 'Отключить все правила' })
  // Tab доходит до кнопки (она в начале вкладки, до блоков)
  await page.getByRole('link', { name: 'Память', exact: true }).focus()
  let reached = false
  for (let i = 0; i < 25 && !reached; i += 1) {
    await page.keyboard.press('Tab')
    reached = await btn.evaluate((n) => n === document.activeElement)
  }
  assert.ok(reached, 'кнопка достижима по Tab')
  const ring = await btn.evaluate((n) => { const cs = getComputedStyle(n); return cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0 })
  assert.ok(ring, 'фокус виден')
  await page.keyboard.press('Enter')
  const d = dialog(page)
  await d.getByRole('heading', { name: 'Отключить все правила' }).waitFor()
  assert.match(await d.innerText(), /Применение всех правил выключится сразу/)
  await page.keyboard.press('Escape')
  await d.waitFor({ state: 'detached' })
  assert.equal(dash.ops('rules_disable_all').filter((m) => m.kind === 'command').length, 0, 'Esc: команды нет')
  await waitFor(() => btn.evaluate((n) => n === document.activeElement), 'фокус вернулся на кнопку')

  await page.keyboard.press('Enter')
  await d.getByRole('button', { name: 'Принимаю риски и применить' }).waitFor()
  await d.getByRole('button', { name: 'Принимаю риски и применить' }).press('Enter')
  await d.getByText('Действие не выполнено').waitFor()
  assert.match(await d.innerText(), /Правила уже не применяются: выключатель записан/, 'частичный успех назван словами')
  assert.deepEqual([...dash.ops('rules_disable_all').find((m) => m.kind === 'command').accepted].sort(), ['all_off', 'block_forever', 'switch_off'])
})

scenario('Аварийные кнопки: доступные имена, достижимы с клавиатуры, видимый фокус', OPTS, async ({ page, open, dash }) => {
  serveMemory(dash)
  await open('/brain/memory')
  await ready(page)
  for (const name of ['Отключить все правила', 'Отключить правило RL0001', 'Отклонить урок L-0004', 'Отклонить урок L-0003']) {
    const b = page.getByRole('button', { name, exact: true })
    assert.equal(await b.count(), 1, `кнопка «${name}»`)
    await b.focus()
    const f = await b.evaluate((n) => ({ active: n === document.activeElement, tab: n.tabIndex, ring: getComputedStyle(n).outlineStyle !== 'none' }))
    assert.ok(f.active && f.tab >= 0 && f.ring, `«${name}»: фокус ${JSON.stringify(f)}`)
  }
})

scenario('Без hello.memory (старый сервер): аварийные кнопки неактивны, команды не уходят', {}, async ({ page, open, dash }) => {
  serveMemory(dash)
  await open('/brain/memory')
  await ready(page)
  assert.equal(await page.getByRole('button', { name: 'Отключить все правила' }).isDisabled(), true)
  await page.getByRole('button', { name: 'Отключить правило RL0001' }).click()
  await page.getByText(/Аварийные действия «Памяти» сейчас недоступны/).waitFor()
  assert.equal(dash.ops('rule_disable').length, 0)
})

// ---- доступность, движение, адаптив

for (const theme of ['dark', 'light']) {
  scenario(`axe-core (${theme === 'dark' ? 'тёмная' : 'светлая'} тема): без серьёзных и критических нарушений на вкладке и в окне подтверждения`, { ...OPTS, colorScheme: theme }, async ({ page, open, dash }) => {
    serveMemory(dash)
    serveActions(dash)
    await open('/brain/memory')
    await ready(page)
    await settle(page)
    await axe(page, 'вкладка')
    await page.getByRole('button', { name: 'Отключить правило RL0001' }).click()
    await dialog(page).getByRole('button', { name: 'Принимаю риски и применить' }).waitFor()
    await settle(page)
    await axe(page, 'окно подтверждения')
  })
}

scenario('axe-core: состояния без данных тоже без серьёзных нарушений', OPTS, async ({ page, open, dash }) => {
  serveMemory(dash, NO_DATA)
  await open('/brain/memory')
  await page.getByText(/Данных пока нет: уроков пока нет/).waitFor()
  await settle(page)
  await axe(page, 'нет данных')
})

scenario('prefers-reduced-motion: вычисленные длительности переходов и анимаций ≤ 10 мс на вкладке и в окне', OPTS, async ({ page, open, dash }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  let release
  const gate = new Promise((r) => { release = r })
  serveMemory(dash, { memory_lessons: 'silence' })
  serveActions(dash)
  dash.replies.memory_lessons = async () => { await gate; return null }
  await open('/brain/memory')
  await ready(page)
  // скелет загрузки (уроки ещё не пришли) тоже без анимации
  const a = await maxDurations(page)
  assert.ok(a.tr <= 10 && a.an <= 10 && a.live <= 10, `вкладка: ${JSON.stringify(a)}`)
  release()
  await page.getByRole('button', { name: 'Отключить правило RL0001' }).click()
  await dialog(page).getByRole('button', { name: 'Принимаю риски и применить' }).waitFor()
  const b = await maxDurations(page)
  assert.ok(b.tr <= 10 && b.an <= 10 && b.live <= 10, `окно: ${JSON.stringify(b)}`)
})

scenario('Без reduced-motion воронка прорисовывается (движение есть), с ним — нет', OPTS, async ({ page, open, dash }) => {
  serveMemory(dash)
  await open('/brain/memory')
  await ready(page)
  const d = await page.locator('.funnel').evaluate((n) => getComputedStyle(n, '::before').transitionDuration)
  assert.notEqual(d, '0s', 'у линии воронки есть переход')
})

for (const theme of ['dark', 'light']) {
  scenario(`Адаптив 390 px (${theme === 'dark' ? 'тёмная' : 'светлая'}): без горизонтальной прокрутки, кнопка «Отключить все правила» видна`, { ...OPTS, viewport: { width: 390, height: 844 }, colorScheme: theme }, async ({ page, open, dash }) => {
    serveMemory(dash)
    await open('/brain/memory')
    await ready(page)
    await settle(page)
    const m = await page.evaluate(() => ({ sw: document.scrollingElement.scrollWidth, cw: document.scrollingElement.clientWidth }))
    assert.ok(m.sw <= m.cw, `горизонтальная прокрутка ${m.sw} > ${m.cw}`)
    assert.ok(await page.getByRole('button', { name: 'Отключить все правила' }).isVisible())
  })
}

scenario('Простой: вкладка не опрашивает сервер по таймеру', { ...OPTS, timeout: 60_000 }, async ({ page, open, dash }) => {
  serveMemory(dash)
  await open('/brain/memory')
  await ready(page)
  const n = OPS.map((op) => dash.ops(op).length)
  await page.waitForTimeout(3000)
  assert.deepEqual(OPS.map((op) => dash.ops(op).length), n, 'новых запросов нет')
})
