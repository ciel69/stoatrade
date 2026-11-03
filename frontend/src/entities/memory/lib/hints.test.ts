import { describe, expect, it } from 'vitest'
import {
  FUNNEL_HINTS, SMALL_TEXT, applyView, blockState, condRu, dateText, ruleText, effectHint, effectView, expiryHint, expiryText, lessonStatusHint, lessonStatusWord, lessonText, normalizeDetail, numView,
  pathLabel, pathSteps, reasonWord, ruleHint, ruleStatusWord, spanText, srcHint, srcWord, stateText, waitHint, waitTermText, waitWord,
} from './hints'
import type { PathRow } from '../model/types'

const RAW = /\b(no_data|restoring|READY|MAX_ACTIVE|LOSS_CAP|SWITCH|LEASE|OWNER_DISABLE|hyp|labels|undefined|NaN|null)\b|[A-Za-z]_[A-Za-z]/
const NOW = 1_800_000_000
const D = 86400

describe('числа с n', () => {
  it('доля и R; n < 30 помечено, n ≥ 30 нет', () => {
    expect(numView({ v: 0.125, n: 12, small: true }, 'pct')).toEqual({ value: '13%', n: 'n = 12', small: true })
    expect(numView({ v: -0.42, n: 40, small: false }, 'r')).toEqual({ value: '−0.42 R', n: 'n = 40', small: false })
    expect(numView({ v: null, n: 0, small: true }, 'pct').value).toBe('–')
    expect(numView(null, 'r').small).toBe(true)
    expect(SMALL_TEXT).toBe('мало данных (n < 30)')
  })
  it('эффект: интервал, единицы метрики, цвет только при выводе и n ≥ 30', () => {
    const e = effectView({ v: -0.31, lo: -0.5, hi: -0.12, n: 64, small: false }, 'mean_r')
    expect(e).toMatchObject({ value: '−0.31 R', interval: 'от −0.50 R до −0.12 R', n: 'n = 64', small: false, tone: 'neg' })
    // доля стопов выше нуля — плохо
    expect(effectView({ v: 0.08, lo: 0.02, hi: 0.14, n: 50, small: false }, 'stop_hit_share')).toMatchObject({ value: '+8.0 п.п.', tone: 'neg' })
    expect(effectView({ v: 0.08, lo: 0.02, hi: 0.14, n: 50, small: false }, 'win_rate').tone).toBe('pos')
    // интервал через ноль и мало данных: без цвета
    expect(effectView({ v: 0.1, lo: -0.1, hi: 0.3, n: 80, small: false }, 'mean_r').tone).toBe('muted')
    expect(effectView({ v: -0.9, lo: -1.5, hi: -0.3, n: 12, small: true }, 'mean_r')).toMatchObject({ small: true, tone: 'muted' })
    expect(effectView({ v: 0.2, lo: null, hi: null, n: 1, small: true }, 'mean_r').interval).toBe('интервала нет')
    expect(effectView(null, 'mean_r').value).toBe('–')
  })
  it('подсказка эффекта объясняет интервал и называет метрику словами', () => {
    const t = effectHint({ claim: { metric: 'mean_r', dir: 'lower', vs: 'base' }, effect: { v: -0.3, lo: -0.5, hi: -0.1, n: 40, small: false } })
    expect(t.text).toMatch(/средний результат сделки/)
    expect(t.text).toMatch(/95%/)
    expect(t.rows).toContainEqual({ label: 'Наблюдений', value: '40' })
  })
})

describe('уроки', () => {
  it('статусы и источники по-русски, с пояснением; код сохраняется в подсказке', () => {
    for (const s of ['hyp', 'confirmed', 'rejected', 'expired', 'merged']) {
      expect(lessonStatusWord(s), s).not.toMatch(/[a-z]/)
      expect(lessonStatusHint(s).text, s).toBeTruthy()
      expect(lessonStatusHint(s).rows).toContainEqual({ label: 'Код', value: s })
    }
    expect(lessonStatusWord('zzz')).toBe('статус неизвестен')
    expect(srcWord('labels')).toBe('из разметки')
    expect(srcWord('brain')).toBe('от мозга')
    expect(srcHint('labels').text).toMatch(/код/)
    expect(lessonStatusHint('rejected', 'OWNER').rows).toContainEqual({ label: 'Причина', value: 'отклонил владелец' })
  })
  it('условие читаемее: знаки и сокращения словами, числа на месте', () => {
    expect(condRu('спред bps >= 5 и сессия = {asia,eu}')).toBe('спред, б.п. ≥ 5 и сессия = «asia,eu»')
    expect(condRu('диапазон 5м % >= 5')).toBe('диапазон 5м, % ≥ 5')
    expect(condRu('funding % <= -0.01')).toBe('ставка финансирования, % ≤ -0.01')
    expect(condRu(null)).toBe('')
    expect(ruleText({ cond_txt: 'RR плана <= 1.5', rule_id: 'RL0001' })).toBe('Не входить, когда прибыль к риску в плане ≤ 1.5')
    expect(ruleText({ cond_txt: null, rule_id: 'RL0001' })).toBe('Правило RL0001')
  })
  it('текст урока: txt, иначе условие, иначе номер', () => {
    expect(lessonText({ txt: 'Не входить ночью', cond_txt: 'час 0–5', lesson_id: 'L-0001' })).toBe('Не входить ночью')
    expect(lessonText({ txt: null, cond_txt: 'час 0–5', lesson_id: 'L-0001' })).toBe('час 0–5')
    expect(lessonText({ txt: null, cond_txt: null, lesson_id: 'L-0001' })).toBe('Урок L-0001')
  })
  it('срок: без срока, вышел, осталось; подсказка отличает уроки разметки', () => {
    expect(expiryText(null, NOW)).toBe('без срока')
    expect(expiryText(NOW - 10, NOW)).toBe('срок вышел')
    expect(expiryText(NOW + 3 * D, NOW)).toBe('ещё 3 дня')
    expect(expiryHint({ src: 'labels', expires_ts: null }, NOW).text).toMatch(/испытание/)
    expect(expiryHint({ src: 'brain', expires_ts: NOW + 2 * 3600 }, NOW).title).toBe('Действует ещё 2 ч')
  })
})

describe('правила', () => {
  it('режим применения: все причины словами, тон', () => {
    expect(applyView({ apply: true, why: 'OK' })).toMatchObject({ title: 'Правила применяются', tone: 'ok' })
    for (const why of ['SWITCH', 'RESTORING', 'DB', 'LEASE']) {
      const v = applyView({ apply: false, why })
      expect(v.title, why).not.toMatch(RAW)
      expect(v.text, why).toBeTruthy()
    }
    expect(applyView({ apply: false, why: 'SWITCH' }).tone).toBe('warn')
    expect(applyView({ apply: false, why: 'XYZ' }).title).toBe('Правила не применяются')
    expect(applyView(null).code).toBe('DB')
  })
  it('ожидание: причина READY/MAX_ACTIVE/LOSS_CAP и срок', () => {
    expect(waitWord('READY')).toBe('готово к включению')
    expect(waitWord('MAX_ACTIVE')).toBe('ждёт места')
    expect(waitWord('LOSS_CAP')).toBe('пауза после потерь')
    expect(waitTermText({ left_s: 2 * D, waited_s: D })).toBe('осталось 2 дня')
    expect(waitTermText({ left_s: -5, waited_s: D })).toBe('срок вышел')
    expect(waitTermText({ left_s: null, waited_s: 3 * 3600 })).toBe('ждёт 3 ч, без срока')
    const h = waitHint({ wait: 'LOSS_CAP', since_ts: NOW - D, deadline_ts: NOW + D })
    expect(h.rows?.map((r) => r.label)).toEqual(['Ждёт с', 'Срок', 'Код'])
  })
  it('статус правила и причины словами; неизвестный код не показывается сырым', () => {
    expect(ruleStatusWord('active')).toBe('действует')
    expect(ruleStatusWord('trial')).toBe('на испытании')
    expect(reasonWord('ROLLBACK')).toMatch(/откат/)
    expect(reasonWord('SOMETHING_NEW')).toBe('по служебной причине')
    expect(ruleHint({ rule_id: 'RL0001', status: 'disabled', reason_code: 'OWNER_DISABLE', cond_txt: 'ночь', ver_in: 'RV2' }).rows).toContainEqual({ label: 'Причина', value: 'отключил владелец' })
  })
})

describe('путь урока', () => {
  const base: PathRow = { candidate: { id: 7, menu_id: 'M1', src: 'labels', status: 'hyp', code: null, p_screen: 0.01, holdout_start: null, holdout_end: null, ts: NOW }, lesson: null, trial: null, rule: null, version: null }
  it('полный путь до версии', () => {
    const p: PathRow = {
      ...base,
      lesson: { lesson_id: 'L-0002', status: 'confirmed', txt: 'x' },
      trial: { trial_id: 'T1', rule_id: 'RL0002', state: 'closed', verdict: 'promote', start_ts: NOW - 9 * D, deadline_ts: NOW, closed_ts: NOW },
      rule: { rule_id: 'RL0002', rev: 2, kind: 'skip_when', status: 'active', wait: null, reason_code: null, from_lesson: 'L-0002', ver_in: 'RV3', ver_out: null, cond_txt: 'x', cond_h: 'h', env_norm: 'paper', ts: '', in_version: true },
      version: { in: 'RV3', in_ts: '', out: null, out_ts: null, out_reason: null },
    }
    const s = pathSteps(p, NOW)
    expect(s.map((x) => x.state)).toEqual(['done', 'done', 'done', 'done', 'done'])
    expect(s[4].word).toBe('в версии RV3')
    expect(pathLabel(s)).toMatch(/^Кандидат: проверен; Урок: подтверждён; Испытание: пройдено; Правило: действует; Версия: в версии RV3$/)
  })
  it('отсеянный кандидат: дальше шагов нет, причина словами', () => {
    const s = pathSteps({ ...base, candidate: { ...base.candidate, status: 'dropped', code: 'NOT_SIGNIFICANT' }, lesson: { lesson_id: 'L-1', status: 'hyp', txt: '' } }, NOW)
    expect(s[0]).toMatchObject({ state: 'fail', word: 'отсеян: разница оказалась случайной' })
    expect(s.slice(1).every((x) => x.state === 'none')).toBe(true)
  })
  it('идёт испытание: срок; правило ждёт места', () => {
    const s = pathSteps({
      ...base, lesson: { lesson_id: 'L-1', status: 'hyp', txt: '' },
      trial: { trial_id: 'T', rule_id: 'R', state: 'open', verdict: null, start_ts: NOW - D, deadline_ts: NOW + 4 * D, closed_ts: null },
      rule: { rule_id: 'RL0003', rev: 1, kind: 'skip_when', status: 'trial', wait: 'MAX_ACTIVE', reason_code: null, from_lesson: 'L-1', ver_in: null, ver_out: null, cond_txt: null, cond_h: null, env_norm: null, ts: null, in_version: false },
    }, NOW)
    expect(s[2]).toMatchObject({ state: 'now', word: 'идёт, ещё 4 дня' })
    expect(s[3]).toMatchObject({ state: 'now', word: 'ждёт места' })
  })
  it('правило снято откатом: версия помечена', () => {
    const s = pathSteps({ ...base, lesson: { lesson_id: 'L-1', status: 'confirmed', txt: '' }, version: { in: 'RV3', in_ts: '', out: 'RV4', out_ts: '', out_reason: 'ROLLBACK' } }, NOW)
    expect(s[4]).toMatchObject({ state: 'fail', word: 'RV3 → снято в RV4' })
  })
  it('у каждого шага воронки есть подсказка', () => {
    for (const k of ['candidates', 'hyp', 'trial', 'confirmed', 'rule_active']) expect(FUNNEL_HINTS[k].text, k).toBeTruthy()
  })
})

describe('состояния блока', () => {
  it('загрузка, связь, ответ; старые данные остаются при повторном запросе', () => {
    expect(blockState(null, true, true)).toBe('loading')
    expect(blockState(null, false, false)).toBe('link')
    expect(blockState({ state: 'no_data' }, false, true)).toBe('no_data')
    expect(blockState({ state: 'ok' }, true, true)).toBe('ok')
  })
  it('причины словами, без сырых кодов; простой текст сервера используется', () => {
    for (const s of ['loading', 'link', 'restoring', 'error', 'no_data'] as const) expect(stateText(s, null, 'об уроках'), s).not.toMatch(RAW)
    expect(stateText('no_data', 'нет данных: уроков пока нет', 'об уроках')).toBe('Данных пока нет: уроков пока нет. Система наберёт их сама, ничего делать не нужно.')
    expect(stateText('no_data', 'no rule_rows table', 'о правилах')).toBe('Данных о правилах пока нет. Система наберёт их сама, ничего делать не нужно.')
    expect(stateText('restoring', null, 'x')).toMatch(/восстанавливается/)
    expect(stateText('error', null, 'о случаях')).toMatch(/Остальная страница работает/)
  })
  it('разбор ответа: чужая форма — ошибка, списки по умолчанию пустые', () => {
    expect(normalizeDetail('memory_rules', 'oops').state).toBe('error')
    expect(normalizeDetail('memory_rules', { ok: true, state: 'weird' }).state).toBe('error')
    const r = normalizeDetail('memory_rules', { ok: true, state: 'no_data', reason: 'нет данных: таблиц цикла нет', mode: { apply: false, why: 'DB', ver: null } }) as { active: unknown[]; mode: { why: string } }
    expect(r.active).toEqual([])
    expect(r.mode.why).toBe('DB')
    const l = normalizeDetail('memory_lessons', { ok: true, state: 'ok', rows: [{ lesson_id: 'L-1' }] }) as { total: number; db: string }
    expect(l.total).toBe(1)
    expect(l.db).toBe('ok')
  })
})

describe('время', () => {
  it('даты и длительности', () => {
    expect(dateText(null)).toBe('–')
    expect(dateText('2026-11-03T14:05:00')).toMatch(/^3 ноя, 14:05$/)
    expect(spanText(90)).toBe('2 мин')
    expect(spanText(5 * D)).toBe('5 дней')
    expect(spanText(D * 1.2)).toBe('1 день')
  })
})
