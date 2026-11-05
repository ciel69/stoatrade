/**
 * Контракт состояния политики выхода для дашборда (R4, только чтение): ответ `exits_status_detail` на операцию `exits_status`.
 * Источник: src/tb/dash2/topics.py::exits_policy -> src/tb/knowledge/exits_cli.py::status_view (тот же отчёт, что `./tb exits status`).
 *
 * Поля файлов (`switch`, `promotion`, `snapshot`, `lease`, `config`) приходят и при `ok:false`; поля базы
 * (`version`, `effective`, `trials`, `watch`) в этом случае пусты. Коды сервера не подменяются: слова для них строит lib/format.ts.
 */

export type ExitsDb = 'OK' | 'NO_DB' | 'RESTORING' | 'DB_ERROR' | 'NOT_READY'
export type ExitsWhy = 'OK' | 'SWITCH' | 'SNAPSHOT' | 'LEASE' | 'HASH'
export type ExitsReason = 'INIT' | 'PROMOTE' | 'ROLLBACK' | 'FLOOR_CHANGED' | 'OWNER_ROLLBACK'
export type ExitsLeaseBy = 'promote' | 'rollback' | 'watch'
export type ExitsSwitchSource = 'file' | 'default' | 'broken'
export type ExitsPromotionMode = 'auto' | 'off'
export type ExitsTrialState = 'open' | 'closed'
export type ExitsTrialVerdict = 'promote' | 'reject' | 'inconclusive'

/** Версия политики: то, что лежит в `exit_versions` (последняя запись). */
export interface ExitsVersion {
  seq: number | null
  ver: string | null
  parent: string | null
  reason_code: string | null
  policy_h: string | null
  floor_h: string | null
  actor: string | null
  /** Секунды epoch. */
  ts: number | null
  numbers: unknown
}

/** Что видит исполнитель (`exitdoc.current`): версия, хеш политики и причина выбора. */
export interface ExitsEffective {
  ver: string | null
  policy_h: string | null
  why: string | null
}

/** Снимок политики `runtime/exit_policy.json`. */
export interface ExitsSnapshot {
  ver: string | null
  policy_h: string | null
  floor_h: string | null
  ts: number | null
}

/** Аренда исполнителя `runtime/exit_lease.json`. */
export interface ExitsLease {
  ts: number | null
  ver: string | null
  policy_h: string | null
  by: string | null
}

/** Выключатель `runtime/exits_switch.json`: файла нет — включено по умолчанию, битый файл — выключено. */
export interface ExitsSwitch {
  apply: boolean | null
  source: string | null
  ts: number | null
  actor: string | null
}

export interface ExitsPromotion {
  mode: string | null
}

export interface ExitsTrialRow {
  trial_id: string | null
  var_id: string | null
  var_ver: number | null
  base_ver: string | null
  state: string | null
  verdict: string | null
  start_ts: number | null
  deadline_ts: number | null
  closed_ts: number | null
  numbers: unknown
}

export interface ExitsTrials {
  open: number
  closed: number
  rows: ExitsTrialRow[]
}

/** Сторож выходов: последняя запись `meta.exits_watch`. */
export interface ExitsWatch {
  ts: number | null
  ver: string | null
  prev: string | null
  codes: string[]
  points: number
  last_point?: unknown
}

export interface ExitsConfig {
  errors: string[]
}

/** Полный ответ `exits_status_detail` в приведённом к фронту виде. */
export interface ExitsPolicy {
  ok: boolean
  /** Момент ответа сервера, секунды epoch. */
  ts: number | null
  db: string
  error: string | null
  version: ExitsVersion | null
  versions: number
  effective: ExitsEffective | null
  snapshot: ExitsSnapshot | null
  lease: ExitsLease | null
  switch: ExitsSwitch | null
  promotion: ExitsPromotion | null
  trials: ExitsTrials
  watch: ExitsWatch | null
  config: ExitsConfig
}
