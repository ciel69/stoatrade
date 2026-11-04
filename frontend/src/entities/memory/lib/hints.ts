import { duration, modeName, plainify, plural, signed } from '@/shared/lib'
import type { TipContent } from '@/shared/ui'
import type {
  BlockState, CasesDetail, Effect, LessonRow, LessonsDetail, MemoryOp, MemoryState, Num, PathDetail, PathRow, RuleBrief, RulesDetail, TrialBrief, WaitingRow,
} from '../model/types'

/**
 * Слова вкладки «Память» для владельца без трейдерской подготовки: что помнит система и как наблюдение становится правилом.
 * Устройство как у entities/idea/lib/hints.ts: коротко (1-2 предложения), термин либо не используется, либо расшифрован, исходный код не теряется (строка «Код» в подсказке).
 * Сырые коды сервера в основной текст не попадают.
 */

export const SMALL_N = 30
const fin = (x: unknown): x is number => typeof x === 'number' && Number.isFinite(x)
const obj = (x: unknown): x is Record<string, unknown> => !!x && typeof x === 'object' && !Array.isArray(x)
const arr = <T>(x: unknown): T[] => (Array.isArray(x) ? (x as T[]) : [])
const cap = (s: string): string => (s ? s[0].toUpperCase() + s.slice(1) : s)
const codeRow = (code: string | null | undefined) => (code ? [{ label: 'Код', value: code }] : [])

// ---------------------------------------------------------------- ответы сервера

const STATES: ReadonlySet<string> = new Set(['ok', 'no_data', 'restoring', 'error'])

/** Ответ операции -> безопасный объект: неизвестная форма становится state «error», пропущенные списки пустыми. Ничего не выдумывает. */
export function normalizeDetail(op: MemoryOp, data: unknown): PathDetail | CasesDetail | LessonsDetail | RulesDetail {
  if (!obj(data) || !STATES.has(String(data.state))) {
    return { ok: false, state: 'error', reason: null, funnel: null, paths: [] } as unknown as PathDetail
  }
  const d = { ...data } as Record<string, unknown>
  d.reason = typeof d.reason === 'string' ? d.reason : null
  if (op === 'memory_cases') {
    d.total = fin(d.total) ? d.total : 0
    d.by_type = obj(d.by_type) ? d.by_type : {}
    d.by_status = obj(d.by_status) ? d.by_status : {}
    d.modes = arr(d.modes)
    d.recent = arr(d.recent)
    d.similar = obj(d.similar) ? d.similar : { state: 'stage3', reason: 'появится на этапе 3' }
  } else if (op === 'memory_lessons') {
    d.rows = arr(d.rows)
    d.by_status = obj(d.by_status) ? d.by_status : {}
    d.by_src = obj(d.by_src) ? d.by_src : {}
    d.total = fin(d.total) ? d.total : arr(d.rows).length
    d.db = STATES.has(String(d.db)) ? d.db : 'ok'
  } else if (op === 'memory_rules') {
    d.mode = obj(d.mode) ? d.mode : { apply: false, why: null, ver: null }
    for (const k of ['versions', 'active', 'waiting', 'rules']) d[k] = arr(d[k])
    d.counts = obj(d.counts) ? d.counts : {}
  } else {
    d.funnel = obj(d.funnel) ? d.funnel : null
    d.paths = arr(d.paths)
  }
  return d as unknown as RulesDetail
}

/** Что показывать в блоке: пока ответа нет — загрузка (или «нет связи»), иначе состояние ответа. */
export function blockState(detail: { state: MemoryState } | null, pending: boolean, linkOpen: boolean): BlockState {
  if (detail && !pending) return detail.state
  if (detail) return detail.state                     // повторный запрос в пути: старые данные остаются на экране
  return linkOpen ? 'loading' : 'link'
}

export const LINK_TEXT = 'Нет связи с платформой, данные не приходят. Страница переподключится сама.'
export const LOADING_TEXT = 'Загружаем данные'

/** Причина пустого или недоступного блока словами. what: о чём блок («уроков», «правил»). Простой русский текст сервера идёт вторым предложением. */
export function stateText(state: BlockState, reason: string | null | undefined, what: string): string {
  if (state === 'loading') return `${LOADING_TEXT}…`
  if (state === 'link') return LINK_TEXT
  if (state === 'restoring') return 'База знаний восстанавливается из резервной копии. Данные появятся сами, когда восстановление закончится; торговля при этом идёт.'
  if (state === 'error') return `Не удалось прочитать данные ${what}. Остальная страница работает; зайдите позже. Если повторяется, сообщите разработчику.`
  if (state === 'no_data') {
    const own = plainify(reason?.replace(/^нет данных:\s*/i, ''))
    return own ? `Данных пока нет: ${own}. Система наберёт их сама, ничего делать не нужно.` : `Данных ${what} пока нет. Система наберёт их сама, ничего делать не нужно.`
  }
  return ''
}

// ---------------------------------------------------------------- числа

export const SMALL_TEXT = `мало данных (n < ${SMALL_N})`
export const SMALL_HINT: TipContent = { title: 'Мало данных', text: `Меньше ${SMALL_N} наблюдений: число может сильно измениться с новыми данными, выводов по нему делать нельзя.` }

export interface NumView { value: string; n: string; small: boolean }

/** Число с размером выборки. kind: r — в R (единицах риска), pct — доля в процентах. */
export function numView(x: Num | null | undefined, kind: 'r' | 'pct'): NumView {
  const n = x && fin(x.n) ? x.n : 0
  const v = x?.v
  const value = !fin(v) ? '–' : kind === 'pct' ? `${Math.round(v * 100)}%` : `${signed(v)} R`
  return { value, n: `n = ${n}`, small: x ? x.small === true || n < SMALL_N : true }
}

/** Единица метрики урока и как читать разность. */
const METRIC: Record<string, { name: string; unit: 'r' | 'pp' }> = {
  mean_r: { name: 'средний результат сделки', unit: 'r' },
  win_rate: { name: 'доля сделок в плюс', unit: 'pp' },
  stop_hit_share: { name: 'доля сделок, закрытых по стопу', unit: 'pp' },
  err_share: { name: 'доля ошибок', unit: 'pp' },
}
export const metricName = (m: string | null | undefined): string => (m && METRIC[m] ? METRIC[m].name : 'показатель')

const fmtEff = (v: number, unit: 'r' | 'pp'): string => (unit === 'r' ? `${signed(v)} R` : `${signed(v * 100, 1)} п.п.`)

export interface EffectView { value: string; interval: string; n: string; small: boolean; tone: 'pos' | 'neg' | 'muted' }

/** Эффект урока с 95% интервалом. Интервал целиком по одну сторону от нуля — вывод есть (цвет), иначе приглушённо. */
export function effectView(e: Effect | null | undefined, metric: string | null | undefined): EffectView {
  const unit = metric && METRIC[metric] ? METRIC[metric].unit : 'r'
  const n = e && fin(e.n) ? e.n : 0
  const small = !e || e.small === true || n < SMALL_N
  if (!e || !fin(e.v)) return { value: '–', interval: '', n: `n = ${n}`, small, tone: 'muted' }
  const has = fin(e.lo) && fin(e.hi)
  const interval = has ? `от ${fmtEff(e.lo as number, unit)} до ${fmtEff(e.hi as number, unit)}` : 'интервала нет'
  // хорошо или плохо зависит от метрики: для стопов и ошибок рост это плохо
  const goodUp = metric !== 'stop_hit_share' && metric !== 'err_share'
  let tone: EffectView['tone'] = 'muted'
  if (has && !small && ((e.lo as number) > 0 || (e.hi as number) < 0)) tone = ((e.lo as number) > 0) === goodUp ? 'pos' : 'neg'
  return { value: fmtEff(e.v, unit), interval, n: `n = ${n}`, small, tone }
}

export function effectHint(l: Pick<LessonRow, 'claim' | 'effect'>): TipContent {
  const name = metricName(l.claim?.metric)
  const vs = l.claim?.vs === 'zero' ? 'нуля' : 'обычных сделок'
  const v = effectView(l.effect, l.claim?.metric)
  return {
    title: 'Эффект урока',
    text: `Насколько ${name} в таких ситуациях отличается от ${vs}. Интервал — где с вероятностью 95% лежит настоящее значение: если он захватывает ноль, разница может быть случайной.`,
    rows: [{ label: 'Разница', value: v.value }, ...(v.interval ? [{ label: 'Интервал 95%', value: v.interval }] : []), { label: 'Наблюдений', value: String(l.effect?.n ?? 0) }],
  }
}

export const N_HINT: TipContent = { title: 'n — число наблюдений', text: `Сколько случаев стоит за числом. Меньше ${SMALL_N} — мало, такие числа помечены.` }

// ---------------------------------------------------------------- время

const MONTH = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек']

/** Дата коротко: «3 ноя, 14:05». ts в секундах или ISO-строка. */
export function dateText(ts: number | string | null | undefined): string {
  const ms = typeof ts === 'number' && Number.isFinite(ts) ? ts * 1000 : typeof ts === 'string' ? Date.parse(ts) : NaN
  if (!Number.isFinite(ms)) return '–'
  const d = new Date(ms)
  return `${d.getDate()} ${MONTH[d.getMonth()]}, ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

/** Длительность словами, с днями: «3 ч 10 мин», «5 дн.». */
export function spanText(s: number | null | undefined): string {
  if (!fin(s)) return '–'
  const a = Math.max(0, s)
  if (a < 86400) return duration(a)
  const d = Math.round(a / 86400)
  return `${d} ${plural(d, ['день', 'дня', 'дней'])}`
}

/** Срок урока: без срока, истёк, осталось. */
export function expiryText(expires: number | null | undefined, now: number): string {
  if (!fin(expires)) return 'без срока'
  return expires <= now ? 'срок вышел' : `ещё ${spanText(expires - now)}`
}

export function expiryHint(l: Pick<LessonRow, 'src' | 'expires_ts'>, now: number): TipContent {
  if (!fin(l.expires_ts)) {
    return {
      title: 'Без срока',
      text: l.src === 'labels' ? 'Уроки из разметки не стареют сами: их судьбу решает испытание правила.' : 'У урока нет срока: он остаётся, пока проверка не опровергнет его.',
    }
  }
  if (l.expires_ts <= now) return { title: 'Срок вышел', text: 'Урок не подтвердился вовремя и скоро уйдёт в устаревшие.', rows: [{ label: 'Срок', value: dateText(l.expires_ts) }] }
  return { title: `Действует ещё ${spanText(l.expires_ts - now)}`, text: 'Если до срока урок не подтвердится на новых сделках, он устареет и перестанет влиять на мозг.', rows: [{ label: 'Срок', value: dateText(l.expires_ts) }] }
}

// ---------------------------------------------------------------- уроки

export type Tone = 'neutral' | 'ok' | 'warn' | 'bad' | 'accent'

const LESSON_STATUS: Record<string, { word: string; tone: Tone; text: string }> = {
  hyp: { word: 'гипотеза', tone: 'accent', text: 'Похоже на закономерность, но ещё не доказано. Код продолжает проверять её на новых данных.' },
  confirmed: { word: 'подтверждён', tone: 'ok', text: 'Закономерность выдержала проверку: правило по уроку прошло испытание и действует.' },
  rejected: { word: 'отклонён', tone: 'neutral', text: 'Урок признан неверным (проверкой или владельцем) и больше не влияет на мозг.' },
  expired: { word: 'устарел', tone: 'neutral', text: 'Урок не подтвердился за отведённое время и выведен из оборота.' },
  merged: { word: 'объединён', tone: 'neutral', text: 'Урок слит с похожим, чтобы не считать одно и то же дважды.' },
}

export const lessonStatusWord = (s: string | null | undefined): string => (s && LESSON_STATUS[s] ? LESSON_STATUS[s].word : 'статус неизвестен')
export const lessonStatusTone = (s: string | null | undefined): Tone => (s && LESSON_STATUS[s] ? LESSON_STATUS[s].tone : 'neutral')
export function lessonStatusHint(s: string | null | undefined, reason?: string | null): TipContent {
  const d = s ? LESSON_STATUS[s] : undefined
  return { title: cap(lessonStatusWord(s)), text: d?.text ?? 'Статус урока, которого мы пока не знаем.', rows: [...codeRow(s), ...(reason ? [{ label: 'Причина', value: reasonWord(reason) }] : [])] }
}

export const SRC_WORD: Record<string, string> = { labels: 'из разметки', brain: 'от мозга' }
export const srcWord = (s: string | null | undefined): string => (s && SRC_WORD[s]) || 'источник неизвестен'
export function srcHint(s: string | null | undefined): TipContent {
  if (s === 'labels') return { title: 'Урок из разметки', text: 'Его нашёл код: он размечает все возможные входы на рынке и ищет условия, при которых они чаще проигрывают. Мозг тут не участвует.' }
  if (s === 'brain') return { title: 'Урок от мозга', text: 'Его записал сам мозг, разбирая свои прошлые сделки. Такие уроки подсказывают мозгу, но не превращаются в правила.' }
  return { title: 'Источник неизвестен', text: 'Откуда взялся урок, платформа не сообщила.' }
}

/**
 * Условие урока или правила (сервер пишет его через tb.memory.lessons.render_cond: «спред bps >= 5 и сессия = {asia}») читаемее:
 * знаки сравнения математическими символами, сокращения словами. Смысл и числа не меняются.
 */
const COND_WORDS: Array<[RegExp, string]> = [
  [/\s>=\s/g, ' ≥ '], [/\s<=\s/g, ' ≤ '], [/\s!=\s/g, ' ≠ '], [/спред bps/g, 'спред, б.п.'], [/RR плана/g, 'прибыль к риску в плане'], [/funding %/g, 'ставка финансирования %'],
  [/метка no-confirm/g, 'вход без подтверждения'], [/блэкаут/g, 'запрет торговли у новостей'], [/\s%/g, ', %'], [/\{([^}]*)\}/g, '«$1»'],
]
export function condRu(c: string | null | undefined): string {
  let t = c ?? ''
  for (const [re, to] of COND_WORDS) t = t.replace(re, to)
  return t
}

/** Главная строка урока: текст урока или, если его нет, условие. */
export const lessonText = (l: Pick<LessonRow, 'txt' | 'cond_txt' | 'lesson_id'>): string => l.txt || condRu(l.cond_txt) || `Урок ${l.lesson_id ?? ''}`.trim()

/** Правило словами: «Не входить, когда …». */
export const ruleText = (r: { cond_txt: string | null; rule_id: string | null }): string => (r.cond_txt ? `Не входить, когда ${condRu(r.cond_txt)}` : `Правило ${r.rule_id ?? ''}`.trim())

// ---------------------------------------------------------------- коды причин (правила, версии, кандидаты)

const REASONS: Record<string, string> = {
  OWNER: 'отклонил владелец', OWNER_REJECT: 'отклонил владелец', OWNER_DISABLE: 'отключил владелец',
  ROLLBACK: 'откат: с правилом стало хуже', TRADE_STARVATION: 'снято: правило слишком часто запрещало входы', BOUNDS_BLOCKED: 'снято: вышло за допустимые границы',
  TRIAL_REJECT: 'не прошло испытание', NO_VERDICT: 'испытание кончилось без вывода', UNREACHABLE_N: 'не наберёт данных до срока', LOSS_CAP: 'отложено из-за потерь',
  SAMPLE_CHANGED: 'данные для проверки изменились', CTX_CHANGED: 'изменились условия расчёта', RESTORED: 'база восстановлена из копии',
  PROMOTE: 'добавлено правило', INIT: 'начальная версия', READY: 'испытание пройдено',
  NOT_SIGNIFICANT: 'разница оказалась случайной', DUP_ACTIVE: 'такой урок уже есть', BUDGET: 'не хватило места в недельной проверке',
  TRIAL: 'поставлено на испытание',
}
/** Причина словами; неизвестный код: общая фраза (сам код — в строке «Код» подсказки). */
export const reasonWord = (c: string | null | undefined): string => (c ? (REASONS[c] ?? 'по служебной причине') : '')

// ---------------------------------------------------------------- правила и rulebook

const RULE_STATUS: Record<string, { word: string; tone: Tone; text: string }> = {
  trial: { word: 'на испытании', tone: 'accent', text: 'Правило проверяют на новых данных: торговлю оно пока не меняет.' },
  active: { word: 'действует', tone: 'ok', text: 'Правило применяется: в похожих ситуациях мозг не входит в сделку.' },
  disabled: { word: 'отключено', tone: 'neutral', text: 'Правило снято и больше не применяется.' },
  rejected: { word: 'отклонено', tone: 'neutral', text: 'Правило не прошло проверку и не применялось.' },
}
export const ruleStatusWord = (s: string | null | undefined): string => (s && RULE_STATUS[s] ? RULE_STATUS[s].word : 'статус неизвестен')
export const ruleStatusTone = (s: string | null | undefined): Tone => (s && RULE_STATUS[s] ? RULE_STATUS[s].tone : 'neutral')
export function ruleHint(r: Pick<RuleBrief, 'rule_id' | 'status' | 'reason_code' | 'cond_txt' | 'ver_in'>): TipContent {
  const d = r.status ? RULE_STATUS[r.status] : undefined
  return {
    title: `Правило ${r.rule_id ?? ''}: ${ruleStatusWord(r.status)}`.trim(),
    text: d?.text ?? 'Состояние правила, которого мы пока не знаем.',
    rows: [
      ...(r.cond_txt ? [{ label: 'Когда', value: r.cond_txt }] : []),
      ...(r.reason_code ? [{ label: 'Причина', value: reasonWord(r.reason_code) }] : []),
      ...(r.ver_in ? [{ label: 'С версии', value: r.ver_in }] : []),
      ...codeRow(r.status),
    ],
  }
}

/** Что делает правило первой итерации: только запрещает вход («не входить, когда …»). */
export const RULE_KIND_TEXT = 'Правило запрещает мозгу входить в сделку, когда выполняется условие. Новых входов оно не добавляет и риск не повышает.'

const WHY: Record<string, { title: string; text: string }> = {
  OK: { title: 'Правила применяются', text: 'Мозг и исполнитель сверяют каждый вход с действующими правилами.' },
  SWITCH: { title: 'Применение правил выключено', text: 'Владелец выключил применение всех правил. Мозг торгует так, будто правил нет. Включают обратно командой в терминале.' },
  RESTORING: { title: 'Правила на паузе: база восстанавливается', text: 'Пока база знаний восстанавливается из копии, правила не применяются. Включатся сами.' },
  DB: { title: 'Правила не применяются: базы правил нет', text: 'База знаний пуста или недоступна. Пока правил нет, мозг торгует без них — это безопасно.' },
  LEASE: { title: 'Правила на паузе: сторож не отвечает', text: 'Сторож, который откатывает вредные правила, давно не выходил на связь. Без него правила не применяются — так безопаснее.' },
}
export function applyView(mode: { apply?: boolean; why?: string | null } | null | undefined): { title: string; text: string; tone: Tone; code: string } {
  const why = mode?.why ?? 'DB'
  const d = WHY[why] ?? { title: mode?.apply ? 'Правила применяются' : 'Правила не применяются', text: 'Причину платформа назвала служебным кодом.' }
  return { ...d, tone: mode?.apply ? 'ok' : why === 'SWITCH' || why === 'LEASE' ? 'warn' : 'neutral', code: why }
}

const WAIT: Record<string, { word: string; text: string }> = {
  READY: { word: 'готово к включению', text: 'Испытание пройдено. Правило включится при ближайшей проверке цикла, после прогрева.' },
  MAX_ACTIVE: { word: 'ждёт места', text: 'Одновременно может действовать ограниченное число правил. Это правило включится, когда освободится место.' },
  LOSS_CAP: { word: 'пауза после потерь', text: 'Недавние потери дошли до предела, поэтому новые правила пока не включаются. Ожидание ограничено сроком.' },
}
export const waitWord = (w: string | null | undefined): string => (w && WAIT[w] ? WAIT[w].word : 'ожидает')
export function waitHint(w: Pick<WaitingRow, 'wait' | 'since_ts' | 'deadline_ts'>): TipContent {
  const d = w.wait ? WAIT[w.wait] : undefined
  return {
    title: cap(waitWord(w.wait)),
    text: d?.text ?? 'Правило ждёт включения по причине, которую мы пока не умеем объяснить.',
    rows: [...(fin(w.since_ts) ? [{ label: 'Ждёт с', value: dateText(w.since_ts) }] : []), ...(fin(w.deadline_ts) ? [{ label: 'Срок', value: dateText(w.deadline_ts) }] : []), ...codeRow(w.wait)],
  }
}
/** Срок ожидания: у паузы после потерь — сколько осталось, у остальных срока нет. */
export function waitTermText(w: Pick<WaitingRow, 'left_s' | 'waited_s'>): string {
  if (fin(w.left_s)) return w.left_s > 0 ? `осталось ${spanText(w.left_s)}` : 'срок вышел'
  return fin(w.waited_s) ? `ждёт ${spanText(w.waited_s)}, без срока` : 'без срока'
}

export const versionReasonWord = (c: string | null | undefined): string => reasonWord(c) || 'новая версия'
export const VERSION_HINT: TipContent = { title: 'Версия набора правил', text: 'Каждое изменение правил записывается новой версией: видно, что и когда поменялось, и можно откатиться.' }

export const FEAT_HINT: TipContent = {
  title: 'Нет данных для проверки',
  text: 'Доля проверок, где правилу не хватило данных о рынке. В такие моменты правило не срабатывает и торговлю не меняет, поэтому это не опасно, но правило работает реже.',
}
export const NO_EVENT_HINT: TipContent = {
  title: 'Решения без события',
  text: 'Доля решений мозга, к которым не нашлось рыночного события для проверки правил или событие запоздало. Если доля выше 20%, платформа подсвечивает это жёлтым: правила работают вслепую. Входы на сигналах вне области правил сюда не входят.',
}

const KIND_WORD: Record<string, string> = {
  patrol: 'плановый обход', book_wall: 'стена в стакане', hot_enter: 'монета стала горячей', range_5m: 'размах за 5 минут', turnover_surge: 'всплеск оборота',
  oi_spike: 'всплеск открытого интереса', liq_cascade: 'каскад ликвидаций',
}
/** Вид сигнала по-русски; незнакомый — как есть (это имя вида сканера, не код ошибки). */
export const kindWord = (k: string): string => KIND_WORD[k] ?? k

export const COVERAGE_HINT: TipContent = {
  title: 'Вне области правил',
  text: 'Доля решений мозга на сигналах, для которых правила не проверялись: правила проверены только на видах сигналов политики проверки и на остальные входы не распространяются. Это не сбой и цветом не подсвечивается.',
}
/** Строка покрытия: «вне области правил: 31% (плановый обход 20, стена в стакане 5)»; нет решений -> ''. */
export function coverageText(c: RulesDetail['coverage'] | null | undefined): string {
  const v = c?.share?.v
  if (!c || !fin(c.n) || c.n <= 0 || !fin(v)) return ''
  const kinds = arr<{ kind: string; n: number }>(c.by_kind).filter((k) => typeof k?.kind === 'string' && fin(k.n) && k.n > 0).slice(0, 4)
  const det = kinds.map((k) => `${kindWord(k.kind)} ${k.n}`).join(', ')
  return `вне области правил: ${Math.round(v * 100)}%${det ? ` (${det})` : ''}`
}
export const noEventText = (yellow: boolean, days: number): string => (yellow ? `Больше 20% за ${days} ${plural(days, ['день', 'дня', 'дней'])}: правила часто проверяются вслепую.` : `За ${days} ${plural(days, ['день', 'дня', 'дней'])}, порог 20%.`)

// ---------------------------------------------------------------- путь «кандидат → урок → испытание → правило → версия»

export const STEP_NAMES = ['Кандидат', 'Урок', 'Испытание', 'Правило', 'Версия'] as const
export type StepState = 'done' | 'now' | 'fail' | 'none'
export interface PathStep { name: (typeof STEP_NAMES)[number]; state: StepState; word: string }

const CAND_STATUS: Record<string, string> = { new: 'ждёт проверки', holdout: 'проверка на свежих данных', hyp: 'стал уроком', dropped: 'отсеян' }

function trialWord(t: TrialBrief | null | undefined, now: number): { state: StepState; word: string } {
  if (!t) return { state: 'none', word: '' }
  if (t.state === 'open') return { state: 'now', word: fin(t.deadline_ts) && t.deadline_ts > now ? `идёт, ещё ${spanText(t.deadline_ts - now)}` : 'идёт' }
  if (t.verdict === 'promote') return { state: 'done', word: 'пройдено' }
  if (t.verdict === 'reject') return { state: 'fail', word: 'не пройдено' }
  return { state: 'fail', word: 'закрыто без вывода' }
}

/** Пять шагов пути одного кандидата. Шаг, на котором путь оборвался, помечен fail; дальше шагов нет (none). */
export function pathSteps(p: PathRow, now: number): PathStep[] {
  const c = p.candidate
  const cand: { state: StepState; word: string } = c.status === 'dropped'
    ? { state: 'fail', word: `отсеян: ${reasonWord(c.code) || 'не прошёл проверку'}` }
    : c.status === 'hyp' || p.lesson ? { state: 'done', word: 'проверен' } : { state: 'now', word: CAND_STATUS[c.status ?? ''] ?? 'на проверке' }
  const lesson: { state: StepState; word: string } = !p.lesson ? { state: 'none', word: '' }
    : p.lesson.status === 'rejected' || p.lesson.status === 'expired' ? { state: 'fail', word: lessonStatusWord(p.lesson.status) }
      : { state: p.lesson.status === 'confirmed' ? 'done' : 'now', word: lessonStatusWord(p.lesson.status) }
  const trial = trialWord(p.trial, now)
  const r = p.rule
  const rule: { state: StepState; word: string } = !r ? { state: 'none', word: '' }
    : r.status === 'active' ? { state: 'done', word: 'действует' }
      : r.status === 'trial' ? { state: 'now', word: r.wait ? waitWord(r.wait) : 'на испытании' }
        : { state: 'fail', word: ruleStatusWord(r.status) }
  const v = p.version
  const version: { state: StepState; word: string } = !v || !v.in ? { state: 'none', word: '' }
    : v.out ? { state: 'fail', word: `${v.in} → снято в ${v.out}` } : { state: 'done', word: `в версии ${v.in}` }
  const all = [cand, lesson, trial, rule, version]
  // после оборванного шага следующих нет, даже если сервер что-то прислал
  const cut = all.findIndex((s) => s.state === 'fail')
  return all.map((s, i) => ({ name: STEP_NAMES[i], ...(cut >= 0 && i > cut ? { state: 'none' as StepState, word: '' } : s) }))
}

/** Подпись строки пути для читалки: «Кандидат: проверен; Урок: гипотеза; …». */
export const pathLabel = (steps: PathStep[]): string => steps.filter((s) => s.state !== 'none').map((s) => `${s.name}: ${s.word}`).join('; ')

export const FUNNEL_HINTS: Record<string, TipContent> = {
  candidates: { title: 'Кандидаты', text: 'Раз в неделю код перебирает условия рынка и отбирает те, где сделки заметно хуже обычного. Каждое такое условие — кандидат.' },
  hyp: { title: 'Уроки', text: 'Кандидаты, которые подтвердились на свежих данных, которых код при поиске не видел. Это уже урок-гипотеза.' },
  trial: { title: 'Испытания', text: 'По уроку составляется правило «не входить, когда …» и проверяется на новых данных, не меняя торговлю.' },
  confirmed: { title: 'Подтверждены', text: 'Уроки, правило по которым прошло испытание.' },
  rule_active: { title: 'Правила действуют', text: 'Правила, которые сейчас применяются к торговле. Вредное правило сторож откатит сам.' },
}

export const PATH_INTRO = 'Система учится сама: код находит закономерность, проверяет её на свежих данных, испытывает правило и только потом включает. Владельцу ничего подтверждать не нужно.'

// ---------------------------------------------------------------- случаи

const CASE_TYPE: Record<string, [string, string, string]> = {
  trade: ['сделка', 'сделки', 'сделок'], skip: ['пропуск', 'пропуска', 'пропусков'], idea: ['идея', 'идеи', 'идей'], signal: ['сигнал', 'сигнала', 'сигналов'],
}
export const caseTypeCount = (t: string, n: number): string => `${n} ${CASE_TYPE[t] ? plural(n, CASE_TYPE[t]) : 'других'}`
const CASE_STATUS: Record<string, string> = { open: 'открыт', closed: 'закрыт', expired: 'истёк', na: 'без итога' }
export const caseStatusWord = (s: string | null | undefined): string => (s && CASE_STATUS[s]) || 'неизвестно'
const SIDE: Record<string, string> = { long: 'лонг', short: 'шорт', watch: 'наблюдение' }
export const sideWord = (s: string | null | undefined): string => (s && SIDE[s]) || ''
const ACTION: Record<string, string> = { enter: 'вход', skip: 'пропуск', wait: 'ожидание', exit: 'выход', stop_move: 'перенос стопа' }
export const caseActionWord = (a: string | null | undefined): string => (a && ACTION[a]) || ''
export const caseModeWord = (m: string | null | undefined): string => modeName(m)

export const CASES_HINT: TipContent = { title: 'Случаи', text: 'Каждое решение мозга (вход, пропуск, идея) записывается случаем вместе с итогом. Из случаев потом ищутся уроки.' }
export const MODES_HINT: TipContent = { title: 'Итоги по режимам', text: 'Закрытые сделки отдельно для бумаги, тестовой биржи и реальных денег: режимы не смешиваются. R — результат в долях риска сделки: +1 R значит заработали столько, сколько рисковали.' }
export const ELIGIBLE_HINT: TipContent = { title: 'Не участвует в статистике', text: 'Случай исключён из подсчётов (например, помечен как подозрительный или из отключённой роли). Он хранится, но на уроки не влияет.' }
export const SIMILAR_TEXT = 'Появится на этапе 3'
export const SIMILAR_HINT: TipContent = { title: 'Поиск похожих случаев', text: 'Мозг сможет перед входом посмотреть, чем кончались похожие ситуации в прошлом. Это следующий этап развития, сейчас его нет.' }

// ---------------------------------------------------------------- аварийные действия

export const EMERGENCY_TEXT = 'Платформа работает без этих кнопок. Они нужны только если правило явно мешает: отключение сразу вступает в силу.'
export const REJECT_HINT: TipContent = { title: 'Отклонить урок', text: 'Урок перестанет проверяться и не станет правилом. Если по нему уже действует правило, оно останется: отключить его можно отдельной кнопкой.' }
export const disableTitle = (id: string | null | undefined): string => `Отключить правило ${id ?? ''}`.trim()
export const DISABLE_WHAT = 'Мозг перестанет учитывать это правило при входе. Платформа больше не предложит его сама.'
export const DISABLE_ALL_TITLE = 'Отключить все правила'
export const DISABLE_ALL_WHAT = 'Мозг будет торговать так, будто правил нет. Включить применение обратно можно только командой в терминале.'
export const ruleCountText = (n: number): string => `${n} ${plural(n, ['правило', 'правила', 'правил'])}`
