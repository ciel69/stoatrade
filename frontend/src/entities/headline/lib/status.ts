import { ageText, clockWord, fixed, impactWord, plural, readerWord, untilWord, type NewsTone } from '@/shared/lib'
import type { NewsSnapshot, NewsSource } from '../model/types'

/**
 * Состояние новостей человеческим языком: одна фраза-вывод, источники, календарь, окно блокировки, читатель. Ничего не выдумывается: нет данных у сервера, нет и фразы.
 * Названия источников, событий и окон приходят из конфигурации платформы: это данные, показываются как текст.
 */

export interface NewsVerdict { tone: NewsTone; text: string }

/** Источник не отвечает или деградирован. */
export const sourceDown = (s: NewsSource): boolean => !s.ok || s.degraded

export interface SourceView {
  id: string
  tone: NewsTone
  /** «работает», «отвечает с перебоями», «не отвечает» */
  state: string
  verified: string
  /** «ответил 5 мин назад» или пусто */
  seen: string
}

export function sourceViews(snap: NewsSnapshot, now: number): SourceView[] {
  return snap.sources.map((s) => ({
    id: s.id,
    tone: s.degraded || !s.ok ? 'warn' : 'ok',
    state: !s.ok ? 'не отвечает' : s.degraded ? 'отвечает с перебоями' : 'работает',
    verified: s.verified ? 'проверен' : 'не проверен',
    seen: typeof s.at === 'number' ? `ответил ${ageText(s.at, now)}` : '',
  }))
}

/** Названия источников с проблемой: из статусов и из списка деградировавших. */
export function downSources(snap: NewsSnapshot): string[] {
  return [...new Set([...snap.sources.filter(sourceDown).map((s) => s.id), ...snap.degraded])]
}

export interface BlackoutView {
  active: boolean
  name: string
  /** начало и конец, секунды; null, если сервер их не прислал */
  from: number | null
  until: number | null
  verified: boolean
  /** одна фраза: идёт сейчас или начнётся */
  text: string
}

/** Окно блокировки. «Новые входы закрыты» говорим только про идущее окно с известным концом; будущее окно описываем как предстоящее. */
export function blackoutView(snap: NewsSnapshot, now: number): BlackoutView | null {
  const b = snap.calendar.blackout
  if (!b) return null
  const from = typeof b.from === 'number' ? b.from : null
  const until = typeof b.to === 'number' ? b.to : null
  const name = b.name?.trim() || 'без названия'
  if (until != null && now > until) return null
  const active = from == null || now >= from
  const text = active
    ? `Новые входы приостановлены${until != null ? ` до ${clockWord(until)}` : ''}: идёт окно вокруг события «${name}».`
    : `Окно вокруг события «${name}» начнётся ${untilWord((from ?? now) - now)}; на это время новые входы закроются.`
  return { active, name, from, until, verified: b.verified !== false, text }
}

export interface EventView {
  id: string
  type: string
  /** «через 2 ч 5 мин» */
  inText: string
  impact: string
  verified: boolean
}

export function eventViews(snap: NewsSnapshot, now: number): EventView[] {
  return snap.calendar.next.filter((e) => e.at > now).map((e) => ({ id: e.id, type: e.type, inText: untilWord(e.at - now), impact: impactWord(e.impact), verified: e.verified }))
}

export interface ReaderLine { label: string; value: string }
export interface ReaderView {
  text: string
  about: string
  tone: NewsTone
  stale: boolean
  /** подробности: профиль, расход, очередь, итоги разметки */
  lines: ReaderLine[]
  /** последняя ошибка модели для раздела «Для специалиста» */
  err: string | null
}

const num = (x: number | null | undefined): x is number => typeof x === 'number' && Number.isFinite(x)

export function readerView(snap: NewsSnapshot, now: number): ReaderView {
  const r = snap.reader
  const w = readerWord(r.enabled, r.status)
  const lines: ReaderLine[] = []
  if (r.enabled) {
    if (r.profile) lines.push({ label: 'Модель', value: r.profile })
    if (num(r.calls_h)) lines.push({ label: 'Запросов за час', value: num(r.max_calls_h) ? `${r.calls_h} из ${r.max_calls_h}` : String(r.calls_h) })
    if (num(r.usd_h) || num(r.usd_day)) {
      const cap = num(r.max_usd_day) ? `, потолок за сутки $${fixed(r.max_usd_day, 2)}` : ''
      lines.push({ label: 'Расход', value: `за час $${fixed(r.usd_h ?? 0, 4)}, за сутки $${fixed(r.usd_day ?? 0, 4)}${cap}` })
    }
    if (num(r.pending)) lines.push({ label: 'Ждут разметки', value: String(r.pending) })
    if (num(r.ok_n) || num(r.fail_n) || num(r.susp_n)) lines.push({ label: 'Итог разметки', value: `размечено ${r.ok_n ?? 0}, не принято ${r.fail_n ?? 0}, подозрительных ${r.susp_n ?? 0}` })
    if (num(r.pause_until) && r.pause_until > now) lines.push({ label: 'Пауза', value: `до ${clockWord(r.pause_until)}` })
    if (num(r.last_ok)) lines.push({ label: 'Последняя разметка', value: ageText(r.last_ok, now) })
  }
  return { ...w, tone: r.enabled && r.stale && w.tone === 'ok' ? 'warn' : w.tone, stale: r.enabled && r.stale, lines, err: r.err ?? null }
}

/**
 * Вывод одной фразой для верхней строки страницы. Порядок важности: идущее окно блокировки, источники, читатель, затем «всё нормально» с ближайшим событием.
 * Выключенный читатель и лимит расходов это справка (idle), а не поломка.
 */
export function newsVerdict(snap: NewsSnapshot, now: number): NewsVerdict {
  const bo = blackoutView(snap, now)
  if (bo?.active) return { tone: 'warn', text: bo.text.replace(/\.$/, '') }

  const total = snap.sources.length
  const down = downSources(snap)
  if (!total && !down.length && !snap.items.length) return { tone: 'idle', text: 'Новостей пока нет: служба новостей не запущена или ещё ничего не собрала' }
  const ev = eventViews(snap, now)[0]
  const evText = ev ? `ближайшее событие: ${ev.type} ${ev.inText}` : ''
  const withEvent = (head: string): string => [head, evText].filter(Boolean).join('; ')

  if (down.length) {
    const all = total > 0 && down.length >= total
    const head = all ? 'Источники новостей не отвечают' : down.length === 1 ? `Источник ${down[0]} не отвечает` : `Не отвечают ${down.length} из ${total || down.length} источников: ${down.join(', ')}`
    const rest = !all && total > down.length ? `, остальные работают` : ''
    return { tone: 'warn', text: head + rest }
  }
  const r = snap.reader
  const rw = readerWord(r.enabled, r.status)
  const ok = total ? `${total} ${plural(total, ['источник работает', 'источника работают', 'источников работают'])}` : ''
  if (!r.enabled) return { tone: 'idle', text: withEvent(`Читатель выключен: заголовки без разметки${ok ? `, ${ok}` : ''}`) }
  if (r.stale) return { tone: 'warn', text: withEvent('Разметка устарела: читатель давно не обновлялся') }
  if (r.status === 'paused' || r.status === 'llm_down') return { tone: 'warn', text: withEvent(`Читатель не размечает: ${rw.text}${ok ? `; ${ok}` : ''}`) }
  if (r.status === 'limit') return { tone: 'idle', text: withEvent(`Читатель ${rw.text}: новые заголовки без разметки${ok ? `; ${ok}` : ''}`) }
  const reader = r.status === 'ok' ? 'читатель размечает' : r.status === 'idle' ? 'новых заголовков нет' : ''
  return { tone: 'ok', text: withEvent(['Новости идут нормально', [ok, reader].filter(Boolean).join(', ')].filter(Boolean).join(': ')) }
}
