import { describe, expect, it } from 'vitest'
import { ideaCaptionHints, ideaDistHint, ideaSideHint, ideaStripHints, ideaTtlHint, NO_LEVEL_TEXT, noteRu, noteTranslated, outcomeHint, setupHint, setupLabel, statCellHint, statusHint, STATUS_HINT } from './hints'
import { STATUS_RU } from './idea'

describe('сетапы', () => {
  it('известные теги получают русское название и пояснение', () => {
    expect(setupLabel('trend_pullback')).toBe('откат по тренду')
    expect(setupHint('trend_pullback')).toMatchObject({ title: 'Откат по тренду', rows: [{ label: 'Код', value: 'trend_pullback' }] })
    expect(setupHint('trend_pullback').text).toMatch(/тренд/)
  })
  it('все сетапы мозга описаны', () => {
    for (const t of ['level_reclaim', 'level_reject', 'sweep_reject', 'breakout_retest', 'range_fade', 'trend_pullback', 'momentum_chase', 'manual', 'none', 'level', 'backfill', 'no-confirm']) {
      expect(setupLabel(t), t).not.toMatch(/[_-]|^[a-z ]+$/)
      expect(setupHint(t).text, t).toBeTruthy()
    }
  })
  it('короткая форма для телефона не длиннее 9 знаков; у неизвестного тега форма одна', () => {
    for (const t of ['level_reclaim', 'level_reject', 'sweep_reject', 'breakout_retest', 'range_fade', 'trend_pullback', 'momentum_chase', 'no-confirm']) expect(setupLabel(t, 'short').length).toBeLessThanOrEqual(9)
    expect(setupLabel('trend_pullback', 'short')).toBe('откат')
    expect(setupLabel('x_y', 'short')).toBe('x y')
  })
  it('подпись чипа не длиннее 17 знаков', () => {
    for (const t of ['level_reclaim', 'level_reject', 'sweep_reject', 'breakout_retest', 'range_fade', 'trend_pullback', 'momentum_chase', 'no-confirm']) expect(setupLabel(t).length).toBeLessThanOrEqual(17)
  })
  it('неизвестный тег показываем как есть, но без подчёркиваний; код сохраняется в подсказке', () => {
    expect(setupLabel('my_new-tag')).toBe('my new tag')
    expect(setupHint('my_new-tag').rows).toEqual([{ label: 'Код', value: 'my_new-tag' }])
  })
})

describe('заметки сканера по-русски', () => {
  it('разбор примера из карточки', () => {
    expect(noteRu('res above price')).toBe('Сопротивление выше цены')
    expect(noteRu('sup below price')).toBe('Поддержка ниже цены')
    expect(noteTranslated('res above price')).toBe(true)
  })
  it('сокращения заменяются словами, числа и русский текст остаются', () => {
    expect(noteRu('патруль без сигнала, цена между уровнями | px 80184, до res 80352 0.21%, до sup 80109 0.09%')).toBe('патруль без сигнала, цена между уровнями | цена 80184, до сопротивления 80352 0.21%, до поддержки 80109 0.09%')
    expect(noteRu('лонг от sup 1.1588 не окупает риск | стоп 1.1331 (2.2%, a5 1.94%), цель res 1.2036')).toBe('лонг от поддержки 1.1588 не окупает риск | стоп 1.1331 (2.2%, размах свечи 5м 1.94%), цель сопротивление 1.2036')
    expect(noteRu('Возврат над pdh 81272 после прокола, цель wh 81795; стоп ниже s5m 81150')).toBe('Возврат над вчерашним максимумом 81272 после прокола, цель максимум недели 81795; стоп ниже экстремум 5м 81150')
  })
  it('русская заметка и тикеры не меняются', () => {
    for (const s of ['Откат к 1ч поддержке.', 'USDT RR 2.1', 'план PL-1029-SOL-02', '', 'TRX']) expect(noteRu(s), s).toBe(s)
    expect(noteRu('USDT RR 2.1')).toBe('USDT RR 2.1')
    expect(noteRu(null)).toBe('')
    expect(noteTranslated('Откат к 1ч поддержке.')).toBe(false)
    expect(noteTranslated(null)).toBe(false)
  })
  it('слова внутри файлов и идентификаторов не трогаем', () => {
    expect(noteRu('см. brief.res.json')).toBe('см. brief.res.json')
  })
})

describe('подсказки карточки', () => {
  it('сторона', () => {
    expect(ideaSideHint('long').title).toMatch(/Лонг/)
    expect(ideaSideHint('short').text).toMatch(/цена упадёт/)
    expect(ideaSideHint(null).title).toBe('Направление не задано')
  })
  it('процент: знак и сторона, у уровня, без цены', () => {
    expect(ideaDistHint({ dist_level_pct: -0.84, zone: false })).toMatchObject({ title: 'Цена на 0.84% ниже уровня' })
    expect(ideaDistHint({ dist_level_pct: -0.84, zone: false }).text).toMatch(/вырасти/)
    expect(ideaDistHint({ dist_level_pct: 5.1, zone: false })).toMatchObject({ title: 'Цена на 5.10% выше уровня' })
    expect(ideaDistHint({ dist_level_pct: 5.1, zone: false }).text).toMatch(/снизиться/)
    expect(ideaDistHint({ dist_level_pct: 0.05, zone: true }).title).toBe('Цена у уровня')
    expect(ideaDistHint({ dist_level_pct: null, zone: false }).title).toBe('Цены нет')
  })
  it('срок: есть, вышел, без срока', () => {
    expect(ideaTtlHint(16 * 3600 + 19 * 60).title).toMatch(/^Осталось 16/)
    expect(ideaTtlHint(16 * 3600).text).toMatch(/истечёт, если цена не дойдёт до уровня/)
    expect(ideaTtlHint(0).title).toBe('Срок вышел')
    expect(ideaTtlHint(null).title).toBe('Без срока')
  })
  it('полоса: метки как в планах, подписи под полосой', () => {
    expect(ideaStripHints.stop).toMatch(/^Стоп/)
    expect(ideaStripHints.level).toMatch(/^Уровень/)
    expect(ideaStripHints.take).toMatch(/^Тейк/)
    expect(ideaStripHints.now).toMatch(/^Цена/)
    expect(ideaCaptionHints.take.title).toBe('Тейк')
  })
  it('идея без уровня объяснена без жаргона', () => {
    expect(NO_LEVEL_TEXT).toMatch(/нет уровня и стопа/)
    expect(NO_LEVEL_TEXT).not.toMatch(/разбудит|сканер/)
  })
})

describe('подсказки строки с исходом', () => {
  it('у каждого статуса есть пояснение', () => {
    for (const s of Object.keys(STATUS_RU) as Array<keyof typeof STATUS_RU>) expect(STATUS_HINT[s], s).toBeTruthy()
    expect(statusHint('expired', STATUS_RU.expired).title).toBe('Истекла')
  })
  it('исход и ячейка статистики', () => {
    expect(outcomeHint(60).title).toBe('Что было бы через 60 мин')
    expect(statCellHint(undefined, 'cont')).toBeNull()
    expect(statCellHint({ n: 0 }, 'cont')).toBeNull()
    expect(statCellHint({ n: 12, cont_gross: 0.5 }, 'cont')).toMatchObject({ title: 'Событий: 12', rows: [{ label: 'До издержек', value: '+0.50%' }] })
    expect(statCellHint({ n: 12 }, 'fade')?.text).toMatch(/Меньше 30/)
    expect(statCellHint({ n: 40 }, 'fade')?.text).toMatch(/Достаточно/)
  })
})
