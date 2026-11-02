import { describe, expect, it } from 'vitest'
import { emptyNote, plural, roleAnswer, roleName, roleWhat, stateWords, thoughtKind, thoughtText, todayText } from './human'

describe('человеческие подписи мозга', () => {
  it('состояние словами и что оно значит для торговли', () => {
    expect(stateWords(true, 'thinking').title).toBe('Думает')
    expect(stateWords(true, 'idle').title).toBe('Отдыхает')
    expect(stateWords(true, 'down').title).toBe('Не отвечает')
    expect(stateWords(true, 'down').hint).toMatch(/исполнитель/)
    expect(stateWords(false, 'down').title).toBe('Подключение')       // до первого снимка не пугаем
  })

  it('пустая лента объясняет состояние', () => {
    expect(emptyNote(false, 'down')).toBe('')
    expect(emptyNote(true, 'down')).toMatch(/не отвечает/)
    expect(emptyNote(true, 'idle')).toMatch(/отдыхает/)
  })

  it('у каждой известной роли имя и фраза без кодов, у неизвестной нейтральная', () => {
    for (const r of ['entry_scout', 'position_manager', 'news_reader', 'morning_analyst', 'day_auditor', 'reviewer']) {
      expect(roleName(r)).not.toMatch(/[A-Za-z_]/)
      expect(roleWhat(r)).not.toMatch(/[A-Za-z_]/)
    }
    expect(roleName('some_new_role')).toBe('Другая роль мозга')
    expect(roleWhat('some_new_role')).not.toContain('some_new_role')
  })

  it('когда роль отвечала', () => {
    expect(roleAnswer(null, 1000)).toBe('ещё не отвечала')
    expect(roleAnswer({ ok: true, t: 880 }, 1000)).toBe('ответила 2 мин назад')
    expect(roleAnswer({ ok: false, t: 880 }, 1000)).toBe('сбой 2 мин назад')
  })

  it('расход за сегодня со склонением', () => {
    expect(plural(1, 'запрос', 'запроса', 'запросов')).toBe('запрос')
    expect(plural(3, 'запрос', 'запроса', 'запросов')).toBe('запроса')
    expect(plural(11, 'запрос', 'запроса', 'запросов')).toBe('запросов')
    expect(todayText(12, '$0.03')).toBe('сегодня: 12 запросов, $0.03')
    expect(todayText(0, '$0.0000')).toBe('сегодня: 0 запросов, $0.0000')
  })

  it('что сделала мысль: без служебных названий решений', () => {
    expect(thoughtKind({ what: 'brain_why', decision: null, ok: true })).toBe('объяснил решение')
    expect(thoughtKind({ what: 'brain_decision', decision: 'move_stop', ok: true })).toBe('подтянул стоп')
    expect(thoughtKind({ what: 'brain_decision', decision: 'weird_new', ok: true })).toBe('принял решение')
    expect(thoughtKind({ what: 'brain_decision', decision: null, ok: false })).toBe('не выполнено')
    expect(thoughtKind({ what: 'brain_decision', decision: null, ok: true })).toBe('разобрал ситуацию')
  })

  it('текст мысли: сетапы словами, коды и файлы убраны, монеты на месте', () => {
    expect(thoughtText('SOL: level_reclaim, стоп за максимум отката')).toBe('SOL: возврат за уровень, стоп за максимум отката')
    expect(thoughtText('отказ STOP_NOT_TIGHTER по плану')).toBe('отказ по плану')
    expect(thoughtText('см. risk_profile.json и no_setup')).toBe('см. файл настроек и no setup')
    expect(thoughtText(null)).toBe('')
  })
})
