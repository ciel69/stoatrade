/**
 * Слова страницы «Мозг · Учится ли мозг»: вердикт, состояния блоков, коды причин, единицы.
 * Смысл взят из .opencode/specs/R0.2-learning-page (requirements 4, 12, 15; таблица кодов причин в design.md).
 * Сырые коды (`no_baseline`, `E_BLOCK`) в интерфейс не попадают: любой неизвестный код получает общую фразу.
 */

export * from './verdict'
export * from './result'

export type LearnVerdictState = 'few_data' | 'no_difference' | 'confirmed'

/** Три слова вердикта. */
export const LEARN_VERDICT_WORD: Readonly<Record<LearnVerdictState, string>> = {
  few_data: 'Данных мало',
  no_difference: 'Разницы не видно',
  confirmed: 'Улучшение подтверждено',
}

/** Что означает слово: одна строка под ним, когда сервер не прислал свой текст. */
export const LEARN_VERDICT_ABOUT: Readonly<Record<LearnVerdictState, string>> = {
  few_data: 'Закрытых сделок меньше 30: сравнивать рано.',
  no_difference: 'Недавние сделки не отличаются от прежних сверх случайного разброса.',
  confirmed: 'Недавние сделки лучше прежних, и это вряд ли случайность. Это не обещание прибыли.',
}

export const LEARN_TREND_DOWN = 'Стало хуже: недавние сделки заметно слабее прежних'

/** Заголовки блоков (запасные: основной заголовок присылает сервер). */
export const LEARN_BLOCK_TITLE: Readonly<Record<string, string>> = {
  result: 'Результат', vs_policy: 'Мозг против теневой политики', discipline: 'Дисциплина', skips: 'Пропуски', exits: 'Выходы',
  economics: 'Экономика', memory: 'Память против базы', lessons: 'Уроки', versions: 'Версии мозга до/после',
}

/** Фразы по кодам причин. Для `no_data` и `few_data` блока, для причины вердикта. */
export const LEARN_REASON: Readonly<Record<string, string>> = {
  n_lt_min: 'Закрытых сделок меньше 30: выводов делать рано',
  no_baseline: 'Сделок мало, чтобы сравнить недавние с прежними (нужно 60)',
  not_significant: 'Разница недавних и прежних сделок в пределах случайного разброса',
  guard_viol: 'R вырос, но доля нарушений правил тоже выросла',
  guard_trades: 'R вырос, но сделок стало заметно меньше',
  no_trades: 'Данных пока нет: в журнале нет закрытых сделок этого режима',
  no_cases: 'Данных пока нет: в памяти нет случаев этого режима',
  no_usage: 'Данных пока нет: журнал вызовов модели пуст',
  no_path: 'Данных пока нет: у сделок нет записанного пути цены',
  no_whatif: 'Данных пока нет: у пропусков нет замера, что было бы дальше',
  mode_unknown: 'Режим пропусков в журнале не определён, чужие цифры не подмешиваются',
  labels_off: 'Метки теневых политик не собираются (сервис labels не работает)',
  improved: 'R недавних сделок вырос, а защиты по нарушениям и числу сделок пройдены',
  few_cases: 'Случаев меньше 30: значения показаны, но выводов делать рано',
  n_eval_lt_min: 'Оценок памяти меньше 30: выводов делать рано',
  computing: 'Считается в фоне',
  too_many_for_request:'Считается по расписанию, появится на этапе 3',
  E_BLOCK: 'Не удалось собрать блок',
  E_REGISTRY: 'Не удалось собрать блок',
}
export const LEARN_REASON_FALLBACK = 'Данных пока нет'

export const learnReason = (code: string | null | undefined): string => (code && LEARN_REASON[code]) || LEARN_REASON_FALLBACK

/** Состояния блока вместо данных. */
export const LEARN_STAGE = (stage: number | null | undefined): string => (typeof stage === 'number' && Number.isFinite(stage) ? `Появится на этапе ${stage}` : 'Появится позже')
export const LEARN_COMPUTING = 'Считается…'
export const LEARN_COMPUTING_TIMEOUT = 'Не удалось дождаться, обновите страницу'
export const LEARN_BLOCK_ERROR = 'Не удалось показать блок'
export const LEARN_REPORT_FAILED = 'Не удалось собрать отчёт'
export const LEARN_LOADING = 'Загружаем отчёт'
export const LEARN_NO_LINK = 'Нет связи с платформой'

/** Подпись значения, у которого сделок меньше порога. */
export const LEARN_SMALL = (min = 30): string => `мало данных (n < ${min})`
/** Расчётная величина: отношение сумм, интервала нет. */
export const LEARN_DERIVED = 'расчётная величина'

/** Единицы. */
export const LEARN_UNIT = { r: 'R', pct: '%', trades: 'сделок', tokens: 'токенов', usd: 'USD' } as const

/** Мера доверия: уровень интервала словами, «95%». */
export const learnLevel = (lvl: number | null | undefined): string => (typeof lvl === 'number' && lvl > 0 && lvl < 1 ? `${Math.round(lvl * 100)}%` : '')

/** Окна справочных графиков. */
export const LEARN_WINDOWS: ReadonlyArray<string> = ['50', '200']

/** Слова вердиктов сравнения с политикой. */
export const LEARN_POLICY_VERDICT: Readonly<Record<string, string>> = { better: 'мозг лучше', worse: 'мозг хуже', unclear: 'не ясно', few_data: 'данных мало' }
export const LEARN_POLICY_SUMMARY: Readonly<Record<string, string>> = {
  above_baseline: 'Мозг выше простых правил', below_baseline: 'Мозг ниже простых правил', unclear: 'Разницы с простыми правилами не видно', few_data: 'Данных для сравнения мало',
}
export const LEARN_CAVEAT: Readonly<Record<string, string>> = {
  unpaired: 'мозг и политики сравниваются не по одним и тем же сигналам',
  selection: 'мозг выбирает сигналы, политики входят на всех',
  costs_model: 'издержки считаются по-разному',
  correlated: 'сделки одного времени связаны, интервалы оптимистичны',
  sim_vs_real: 'политики симулируются, а сделки мозга настоящие',
}

/** Сигналы блоков: направление за последние 4 недели против предыдущих 4. */
export const LEARN_SIGNAL: Readonly<Record<string, string>> = {
  falling: 'снижается', rising: 'растёт', flat: 'без изменений', improving: 'улучшается', worse: 'ухудшается',
}
