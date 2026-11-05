import type {
  ExitsConfig, ExitsEffective, ExitsLease, ExitsPolicy, ExitsPromotion, ExitsSnapshot, ExitsSwitch, ExitsTrialRow, ExitsTrials, ExitsVersion, ExitsWatch,
} from '../model/types'

/** Пустое значение: неизвестное число, время или поле показываем прочерком, а не догадкой. */
export const DASH = '—'

const isRec = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v)
const str = (v: unknown): string | null => (typeof v === 'string' && v.trim() ? v : null)
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)
const strList = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [])

const MONTH = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек']

/** Время в секундах epoch по местному времени: «4 ноя, 21:10»; неизвестное — «—». */
export function dateText(ts: number | null | undefined): string {
  if (typeof ts !== 'number' || !Number.isFinite(ts)) return DASH
  const d = new Date(ts * 1000)
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  return `${d.getDate()} ${MONTH[d.getMonth()]}, ${hh}:${mm}`
}

// ---------------------------------------------------------------- причины версии

const REASON_WORD: Readonly<Record<string, string>> = {
  INIT: 'создана',
  PROMOTE: 'продвинута',
  ROLLBACK: 'откат',
  FLOOR_CHANGED: 'пол изменён',
  OWNER_ROLLBACK: 'откат владельца',
}

/** Причина версии по-русски; неизвестный код остаётся кодом, но с русским словом. */
export function reasonWord(code: string | null | undefined): string {
  if (!code) return 'причина не указана'
  return REASON_WORD[code] ?? `код ${code}`
}

// ---------------------------------------------------------------- что видит исполнитель

const WHY_WORD: Readonly<Record<string, string>> = {
  OK: 'всё сходится',
  SWITCH: 'применение выключено',
  SNAPSHOT: 'снимка нет',
  LEASE: 'аренда просрочена',
  HASH: 'хеши разошлись',
}

const WHY_TEXT: Readonly<Record<string, string>> = {
  OK: 'снимок, аренда и файлы совпадают — политика применяется',
  SWITCH: 'выключатель запрещает исполнителю применять политику',
  SNAPSHOT: 'снимка нет или он битый — исполнитель не может взять версию из файла',
  LEASE: 'срок аренды исполнителя вышел, нужна новая',
  HASH: 'снимок и аренда разошлись: хеш политики не совпадает',
}

/** Короткое слово выбора исполнителя: OK, SWITCH, SNAPSHOT, LEASE, HASH. */
export function whyWord(why: string | null | undefined): string {
  if (!why) return 'причина не указана'
  return WHY_WORD[why] ?? `код ${why}`
}

/** Одна строка пояснения к выбору исполнителя. */
export function whyText(why: string | null | undefined): string {
  if (!why) return 'сервер не назвал причину'
  return WHY_TEXT[why] ?? 'причина не распознана'
}

// ---------------------------------------------------------------- выключатель, режим, аренда

/** Слово выключателя: включено / ВЫКЛЮЧЕНО; null — неизвестно. */
export function switchWord(sw: ExitsSwitch | null | undefined): string {
  if (!sw || sw.apply == null) return DASH
  return sw.apply ? 'включено' : 'ВЫКЛЮЧЕНО'
}

/** Пояснение к выключателю: откуда взято состояние. */
export function switchNote(sw: ExitsSwitch | null | undefined): string {
  if (!sw) return 'состояние выключателя неизвестно'
  if (sw.source === 'default') return 'файла выключателя нет — включено по умолчанию'
  if (sw.source === 'file') return sw.apply ? 'задано владельцем' : 'выключено владельцем'
  if (sw.source === 'broken') return 'файл повреждён, поэтому применение выключено'
  return 'источник выключателя не распознан'
}

const PROMOTION_WORD: Readonly<Record<string, string>> = { auto: 'авто', off: 'выключено' }
const PROMOTION_NOTE: Readonly<Record<string, string>> = {
  auto: 'новые версии появляются по итогам испытаний',
  off: 'версии не продвигаются автоматически',
}

/** Режим продвижения: auto / off по-русски. */
export function promotionWord(mode: string | null | undefined): string {
  if (!mode) return DASH
  return PROMOTION_WORD[mode] ?? `код ${mode}`
}

/** Пояснение к режиму продвижения. */
export function promotionNote(mode: string | null | undefined): string {
  if (!mode) return 'режим неизвестен'
  return PROMOTION_NOTE[mode] ?? 'режим не распознан'
}

const LEASE_BY_WORD: Readonly<Record<string, string>> = { promote: 'продвижение', rollback: 'откат', watch: 'сторож' }

/** Кто взял аренду: продвижение, откат или сторож. */
export function leaseByWord(by: string | null | undefined): string {
  if (!by) return 'кто взял — неизвестно'
  return LEASE_BY_WORD[by] ?? `код ${by}`
}

// ---------------------------------------------------------------- испытания, сторож, база

const TRIAL_VERDICT_WORD: Readonly<Record<string, string>> = { promote: 'продвинуто', reject: 'отклонено', inconclusive: 'без вывода' }

/** Вердикт закрытого испытания по-русски. */
export function trialVerdictWord(verdict: string | null | undefined): string {
  if (!verdict) return DASH
  return TRIAL_VERDICT_WORD[verdict] ?? `код ${verdict}`
}

const WATCH_CODE_WORD: Readonly<Record<string, string>> = { EXIT_SIM_GAP: 'расхождение факта и симуляции' }

/** Код сторожа по-русски; код остаётся рядом в скобках, чтобы его можно было найти в документации. */
export function watchCodeWord(code: string): string {
  return WATCH_CODE_WORD[code] ?? `код ${code}`
}

/** Коды сторожа строкой: «расхождение факта и симуляции (EXIT_SIM_GAP)»; пусто — «кодов нет». */
export function watchCodesText(codes: readonly string[] | null | undefined): string {
  if (!codes?.length) return 'кодов нет'
  return codes.map((c) => `${watchCodeWord(c)} (${c})`).join('; ')
}

const DB_MESSAGE: Readonly<Record<string, string>> = {
  OK: 'база знаний в порядке',
  NO_DB: 'база знаний ещё не создана',
  NOT_READY: 'таблиц цикла выходов нет — появятся после первого прохода цикла',
  RESTORING: 'база восстанавливается',
  DB_ERROR: 'ошибка чтения базы',
}

/** Человеческая причина недоступности базы; код причины карточка показывает рядом. */
export function dbMessage(code: string | null | undefined): string {
  if (!code) return 'база знаний не ответила'
  return DB_MESSAGE[code] ?? `база недоступна (код ${code})`
}

// ---------------------------------------------------------------- приведение ответа

function normVersion(raw: unknown): ExitsVersion | null {
  if (!isRec(raw)) return null
  return {
    seq: num(raw.seq),
    ver: str(raw.ver),
    parent: str(raw.parent),
    reason_code: str(raw.reason_code),
    policy_h: str(raw.policy_h),
    floor_h: str(raw.floor_h),
    actor: str(raw.actor),
    ts: num(raw.ts),
    numbers: raw.numbers ?? null,
  }
}

function normEffective(raw: unknown): ExitsEffective | null {
  if (!isRec(raw)) return null
  return { ver: str(raw.ver), policy_h: str(raw.policy_h), why: str(raw.why) }
}

function normSnapshot(raw: unknown): ExitsSnapshot | null {
  if (!isRec(raw)) return null
  return { ver: str(raw.ver), policy_h: str(raw.policy_h), floor_h: str(raw.floor_h), ts: num(raw.ts) }
}

function normLease(raw: unknown): ExitsLease | null {
  if (!isRec(raw)) return null
  return { ts: num(raw.ts), ver: str(raw.ver), policy_h: str(raw.policy_h), by: str(raw.by) }
}

function normSwitch(raw: unknown): ExitsSwitch | null {
  if (!isRec(raw)) return null
  return {
    apply: typeof raw.apply === 'boolean' ? raw.apply : null,
    source: str(raw.source),
    ts: num(raw.ts),
    actor: str(raw.actor),
  }
}

function normPromotion(raw: unknown): ExitsPromotion | null {
  if (!isRec(raw)) return null
  return { mode: str(raw.mode) }
}

function normTrialRow(raw: unknown): ExitsTrialRow | null {
  if (!isRec(raw)) return null
  return {
    trial_id: str(raw.trial_id),
    var_id: str(raw.var_id),
    var_ver: num(raw.var_ver),
    base_ver: str(raw.base_ver),
    state: str(raw.state),
    verdict: str(raw.verdict),
    start_ts: num(raw.start_ts),
    deadline_ts: num(raw.deadline_ts),
    closed_ts: num(raw.closed_ts),
    numbers: raw.numbers ?? null,
  }
}

function normTrials(raw: unknown): ExitsTrials {
  const r = isRec(raw) ? raw : {}
  const rows = Array.isArray(r.rows) ? r.rows.map(normTrialRow).filter((x): x is ExitsTrialRow => x !== null) : []
  return { open: num(r.open) ?? 0, closed: num(r.closed) ?? 0, rows }
}

function normWatch(raw: unknown): ExitsWatch | null {
  if (!isRec(raw)) return null
  return {
    ts: num(raw.ts),
    ver: str(raw.ver),
    prev: str(raw.prev),
    codes: strList(raw.codes),
    points: num(raw.points) ?? 0,
    last_point: raw.last_point ?? null,
  }
}

function normConfig(raw: unknown): ExitsConfig {
  const r = isRec(raw) ? raw : {}
  return { errors: strList(r.errors) }
}

/**
 * Приведение ответа `exits_status_detail` к виду карточки. Не объект — null (ответ не наш).
 * Отсутствующие файловые поля остаются null: карточка покажет «—», а не выдуманное состояние.
 */
export function normalizeExits(raw: unknown): ExitsPolicy | null {
  if (!isRec(raw)) return null
  // У ответа всегда есть ok или db; объект без них — чужая или битая форма, а не «база недоступна».
  if (typeof raw.ok !== 'boolean' && typeof raw.db !== 'string') return null
  return {
    ok: raw.ok === true,
    ts: num(raw.ts),
    db: str(raw.db) ?? '',
    error: str(raw.error),
    version: normVersion(raw.version),
    versions: num(raw.versions) ?? 0,
    effective: normEffective(raw.effective),
    snapshot: normSnapshot(raw.snapshot),
    lease: normLease(raw.lease),
    switch: normSwitch(raw.switch),
    promotion: normPromotion(raw.promotion),
    trials: normTrials(raw.trials),
    watch: normWatch(raw.watch),
    config: normConfig(raw.config),
  }
}
