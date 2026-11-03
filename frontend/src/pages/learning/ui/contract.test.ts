import { existsSync, readFileSync, readdirSync } from 'node:fs'
import { resolve } from 'node:path'
import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  BLOCK_STATUSES, blockView, estView, normalizeLearning, verdictView, type DisciplineBlock, type ExitsBlock, type HypsBlock, type LearningBlock, type LearningDetail, type LessonsBlock,
  type SkipsBlock, type VersionsBlock,
} from '@/entities/learning'
import { LEARN_CAVEAT, LEARN_GUARD_MARK, LEARN_SIGNAL, learnReason } from '@/shared/lib'
import { LearningDiscipline } from '@/widgets/learning/discipline'
import { LearningEconomics } from '@/widgets/learning/economics'
import { LearningExits } from '@/widgets/learning/exits'
import { LearningHyps } from '@/widgets/learning/hyps'
import { LearningLessons } from '@/widgets/learning/lessons'
import { LearningMemory } from '@/widgets/learning/memory'
import { LearnResult } from '@/widgets/learning/result'
import { LearningSkips } from '@/widgets/learning/skips'
import { Stages } from '@/widgets/learning/stages'
import { LearnVerdict } from '@/widgets/learning/verdict'
import { LearningVersions } from '@/widgets/learning/versions'
import { VsPolicy } from '@/widgets/learning/vs-policy'

/**
 * Сверка контракта tb.learning/1: эталоны бэкенда (tests/fixtures_learning/*.json, строит tests/_learn_report_helpers.py)
 * проходят приведение фронта без потерь и отображаются виджетами без ошибок.
 * Файлы читаются прямо из репозитория (путь от корня frontend/, тест запускается из него); копий нет, расхождение не спрячется.
 */
const DIR = resolve(process.cwd(), '..', 'tests', 'fixtures_learning')
type Json = Record<string, any>

const files = existsSync(DIR) ? readdirSync(DIR).filter((f) => /^payload_.*\.json$/.test(f)).sort() : []
const load = (f: string): Json => JSON.parse(readFileSync(resolve(DIR, f), 'utf8')) as Json

const isNumOrNull = (x: unknown): boolean => x === null || (typeof x === 'number' && Number.isFinite(x))

/** Все оценки (объекты с ключами lvl и v) имеют форму {v, lo, hi, n, lvl}: число или null, n целое. У блоков versions и hyps lvl — уровень интервалов блока, не оценка. */
function checkEsts(x: unknown, path: string, errors: string[]): void {
  if (Array.isArray(x)) { x.forEach((e, i) => checkEsts(e, `${path}[${i}]`, errors)); return }
  if (!x || typeof x !== 'object') return
  const o = x as Json
  if ('lvl' in o && 'v' in o) {
    for (const k of ['v', 'lo', 'hi']) if (!isNumOrNull(o[k])) errors.push(`${path}.${k}`)
    if (!Number.isInteger(o.n)) errors.push(`${path}.n`)
    if (typeof o.lvl !== 'number') errors.push(`${path}.lvl`)
  }
  for (const [k, v] of Object.entries(o)) checkEsts(v, `${path}.${k}`, errors)
}

const FALLBACK = learnReason('__нет_такого_кода__')
const BAD_TEXT =/NaN|undefined|Infinity|\[object|\bnull\b/

describe('эталоны бэкенда tb.learning/1', () => {
  it('найдены все десять сценариев', () => {
    expect(files).toEqual([
      'payload_computing.json', 'payload_confirmed.json', 'payload_down.json', 'payload_empty.json', 'payload_few.json',
      'payload_full.json', 'payload_mode_unknown.json', 'payload_no_labels.json', 'payload_nodiff.json', 'payload_r2.json',
    ])
  })

  describe.each(files)('%s', (file) => {
    let warn: ReturnType<typeof vi.spyOn>
    let err: ReturnType<typeof vi.spyOn>
    beforeEach(() => { warn = vi.spyOn(console, 'warn').mockImplementation(() => {}); err = vi.spyOn(console, 'error').mockImplementation(() => {}) })
    afterEach(() => { warn.mockRestore(); err.mockRestore() })

    const raw = load(file)
    const d = normalizeLearning(raw) as LearningDetail

    it('проходит приведение без потерь: верхний уровень, порядок, мета, вердикт', () => {
      expect(d.ok).toBe(true)
      expect(d.mode).toBe(raw.mode)
      expect(d.asof).toBe(raw.asof)
      expect(d.min_n).toBe(raw.min_n)
      expect(d.alpha).toBe(raw.alpha)
      expect(d.order).toEqual(raw.order)
      expect(Object.keys(d.blocks).sort()).toEqual(Object.keys(raw.blocks).sort())
      expect(d.meta).toEqual(raw.meta)
      expect(d.verdict).toMatchObject({ ...raw.verdict })
      expect(d.verdict.guards).toEqual(raw.verdict.guards)
      expect(d.verdict.params).toEqual(raw.verdict.params)
    })

    it('блоки: все поля сервера сохранены, статусы известны, причины и этапы на месте', () => {
      for (const [id, rb] of Object.entries<Json>(raw.blocks)) {
        const b = d.blocks[id]!
        expect(BLOCK_STATUSES, id).toContain(rb.status)
        expect(b, id).toMatchObject(rb)
        expect(b.status, id).toBe(rb.status)
        expect(b.reason ?? null, id).toBe(rb.reason ?? null)
        if (rb.status === 'stage') { expect(typeof b.stage, id).toBe('number'); expect(typeof b.roadmap, id).toBe('string') }
        if (rb.status === 'no_data') expect(blockView(b).kind, id).toBe('empty')
      }
    })

    it('оценки везде в форме Est (число или null), n целое', () => {
      const errors: string[] = []
      checkEsts(raw, file, errors)
      expect(errors).toEqual([])
    })

    it('коды причин, оговорок и сигналов известны фронту', () => {
      if (raw.verdict.reason) expect(learnReason(raw.verdict.reason), `verdict ${raw.verdict.reason}`).not.toBe(FALLBACK)
      for (const [id, rb] of Object.entries<Json>(raw.blocks)) {
        if (rb.reason) expect(learnReason(rb.reason), `${id} ${rb.reason}`).not.toBe(FALLBACK)
        for (const c of rb.caveats ?? []) expect(LEARN_CAVEAT[c], `${id} caveat ${c}`).toBeTruthy()
        if (typeof rb.signal === 'string') expect(LEARN_SIGNAL[rb.signal], `${id} signal ${rb.signal}`).toBeTruthy()
      }
    })

    it('вердикт: слово, текст сервера и защиты отражают поля', () => {
      const v = verdictView(d.verdict)
      expect(v.state).toBe(raw.verdict.state)
      expect(v.text).toBe(raw.verdict.text)
      expect(v.warn !== '').toBe(raw.verdict.trend === 'down')
      const st = (g: Json | null): 'ok' | 'bad' | 'none' => (g && typeof g.ok === 'boolean' ? (g.ok ? 'ok' : 'bad') : 'none')
      const w = mount(LearnVerdict, { props: { verdict: d.verdict } })
      const t = w.text()
      expect(t).toContain(raw.verdict.text)
      // обе защиты выведены словом состояния (для guard_unknown вместо «пройдена» стоит «не проверялась»)
      const g = raw.verdict.guards
      const want = [st(g.viol), st(g.trades_day)].map((s, i) => (i === 0 && s === 'ok' && g.viol.guard_unknown ? 'none' : s))
      for (const s of new Set(want)) expect(t, `guards ${s}`).toContain(LEARN_GUARD_MARK[s])
      expect(w.text()).not.toMatch(BAD_TEXT)
    })

    it('мозг против политик: строки, ctx_changed и оговорки не теряются', () => {
      const b = raw.blocks.vs_policy as Json
      const text = mount(VsPolicy, { props: { block: d.blocks.vs_policy! } }).text()
      for (const r of b.rows as Json[]) expect(text, r.id).toContain(String(r.name).replace(/_/g, ' '))
      if (b.status === 'ok') for (const c of b.caveats as string[]) expect(text, c).toContain(LEARN_CAVEAT[c])
    })

    it('все виджеты рисуются без предупреждений и мусорных значений', () => {
      const blocks = d.order.map((id) => d.blocks[id]!) as LearningBlock[]
      const texts: Array<[string, string]> = []
      const put = (id: string, html: string): void => { texts.push([id, html]) }
      for (const b of blocks) {
        switch (b.id) {
          case 'result': put(b.id, mount(LearnResult, { props: { block: b } }).text()); break
          case 'vs_policy': put(b.id, mount(VsPolicy, { props: { block: b } }).text()); break
          case 'discipline': put(b.id, mount(LearningDiscipline, { props: { block: b as DisciplineBlock } }).text()); break
          case 'skips': put(b.id, mount(LearningSkips, { props: { block: b as SkipsBlock } }).text()); break
          case 'exits': put(b.id, mount(LearningExits, { props: { block: b as ExitsBlock } }).text()); break
          case 'economics': put(b.id, mount(LearningEconomics, { props: { block: b } }).text()); break
          case 'memory': put(b.id, mount(LearningMemory, { props: { block: b } }).text()); break
          case 'lessons': put(b.id, mount(LearningLessons, { props: { block: b } }).text()); break
          case 'versions': put(b.id, mount(LearningVersions, { props: { block: b } }).text()); break
          case 'hyps': put(b.id, mount(LearningHyps, { props: { block: b } }).text()); break
          default: break
        }
      }
      if (blocks.some((b) => b.status === 'stage')) put('stages', mount(Stages, { props: { blocks } }).text())      // в r2 заглушек этапа нет
      for (const [id, t] of texts) {
        expect(t.length, id).toBeGreaterThan(0)
        expect(t, id).not.toMatch(BAD_TEXT)
      }
      expect(warn).not.toHaveBeenCalled()
      expect(err).not.toHaveBeenCalled()
    })
  })

  it('full: окна результата, выходы и политики с данными', () => {
    const d = normalizeLearning(load('payload_full.json'))!
    expect(d.verdict.state).toBe('confirmed')
    expect(Object.keys((d.blocks.result as Json).windows).sort()).toEqual(['200', '50'])
    expect(d.blocks.vs_policy!.status).toBe('ok')
    expect(d.blocks.economics!.status).toBe('ok')
  })

  it('few: значения показаны со статусом few_data, у блоков причины как есть', () => {
    const d = normalizeLearning(load('payload_few.json'))!
    expect(d.verdict.state).toBe('few_data')
    expect(d.blocks.result!.status).toBe('few_data')
    expect(blockView(d.blocks.result!).few).toBe(true)
  })

  it('computing: блок памяти показывает «Считается…»', () => {
    const d = normalizeLearning(load('payload_computing.json'))!
    expect(d.blocks.memory!.status).toBe('computing')
    expect(mount(LearningMemory, { props: { block: d.blocks.memory! } }).text()).toContain('Считается')
  })

  describe('r2: блоки R2.6 и «Гипотезы» показывают числа ответа бэкенда', () => {
    const raw = load('payload_r2.json')
    const d = normalizeLearning(raw)!
    const r = (e: Json | null): string => estView(e as never, 'r').value

    it('без таблиц цикла (full, empty): уроки и версии — заглушка этапа 2, гипотезы — причина словами', () => {
      for (const f of ['payload_full.json', 'payload_empty.json']) {
        const x = normalizeLearning(load(f))!
        const st = mount(Stages, { props: { blocks: x.order.map((id) => x.blocks[id]!) } }).text()
        expect(st, f).toMatch(/Уроки[\s\S]*Появится на этапе 2/)
        expect(st, f).toContain('Версии мозга до/после')
        expect(mount(LearningHyps, { props: { block: x.blocks.hyps! } }).text(), f).toContain('Бумажных сделок с гипотезой пока нет')
      }
    })

    it('уроки: воронка, статусы и лучший урок с n и интервалом', () => {
      const b = raw.blocks.lessons as Json
      const t = mount(LearningLessons, { props: { block: d.blocks.lessons as LessonsBlock } }).text()
      for (const k of ['candidates', 'hyp', 'trials', 'confirmed', 'rules']) expect(t, k).toContain(String(b.funnel[k]))
      expect(t).toContain(`закончено ${b.funnel.trials_closed}`)
      expect(t).toContain(`${b.n_lessons} уроков`)
      expect(t).toContain(b.best.text)
      expect(t).toContain(`n = ${b.best.n}`)
      expect(t).toContain(r(b.best.diff))
      expect(t).toContain(r({ ...b.best.diff, v: b.best.diff.lo }))
      expect(t).toContain(r({ ...b.best.diff, v: b.best.diff.hi }))
    })

    it('версии: каждая смена, стороны до/после с n, вердикты словами', () => {
      const b = raw.blocks.versions as Json
      const t = mount(LearningVersions, { props: { block: d.blocks.versions as VersionsBlock } }).text()
      const WORD: Record<string, string> = { few_data: 'данных мало', worse: 'хуже', not_worse: 'не хуже' }
      for (const p of [...b.rulebook, ...b.swaps] as Json[]) {
        expect(t).toContain(`${p.before.n} сдел`)
        expect(t).toContain(`${p.after.n} сдел`)
        if (p.after.r.v !== null) expect(t).toContain(r(p.after.r))
        if (p.diff.v !== null) expect(t).toContain(estView(p.diff, 'r', 2, 0).interval)
        expect(t).toContain(WORD[p.verdict])
      }
      for (const p of b.rulebook as Json[]) expect(t).toContain(`Правила ${p.from} → ${p.to}`)
      expect(t).toContain('Смена модели')
      expect(t).toContain('для вывода нужно 100')
      expect(t).toContain(`${b.n_pre_r2} сделок без версии правил`)
    })

    it('гипотезы: прогресс n из 30, средний R с интервалом, статус и справка меток', () => {
      const b = raw.blocks.hyps as Json
      const w = mount(LearningHyps, { props: { block: d.blocks.hyps as HypsBlock } })
      const t = w.text()
      const STATUS: Record<string, string> = { confirmed: 'подтверждена', accumulating: 'копит', not_confirmed: 'не подтверждена' }
      for (const f of b.families as Json[]) {
        expect(t, f.family).toContain(f.n >= 30 ? `${f.n} сделок, порог 30 пройден` : `${f.n} из 30`)
        expect(t, f.family).toContain(STATUS[f.status])
        if (f.n) expect(t, f.family).toContain(`${r(f.r)}`)
        if (f.n) expect(t, f.family).toContain(estView(f.r, 'r').interval)
        if (f.ref) expect(t, f.family).toContain(`n = ${f.ref.n}`)
      }
      expect(t.match(/Что говорят тысячи сигналов/g)?.length).toBe((b.families as Json[]).filter((f) => f.ref).length)
      expect(w.findAll('[role="meter"]').length).toBe((b.families as Json[]).length)
      expect(t).toContain(`подтверждено ${b.n_confirmed} из ${(b.families as Json[]).length}`)
    })
  })
})
