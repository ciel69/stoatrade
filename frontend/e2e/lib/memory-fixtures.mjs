/**
 * Ответы операций вкладки «Память» по схемам src/tb/dash2/memory_ops.py (ключи сверены с tests/test_dash2_memory.py: CASES_KEYS, LESSONS_KEYS, RULES_KEYS, PATH_KEYS ...).
 * Числа {v, n, small}: small = n < 30. Время в секундах от «сейчас», чтобы сроки были живыми.
 */
const D = 86400
const num = (v, n) => ({ v, n, small: n < 30 })
const eff = (v, lo, hi, n) => ({ ...num(v, n), lo, hi })

export const MEMORY_HELLO = {
  csrf: 'e2e-mem-csrf',
  actions: ['lesson_reject', 'rule_disable', 'rules_disable_all'],
  confirm: { lesson_reject: [], rule_disable: ['rule_off', 'block_forever'], rules_disable_all: ['switch_off', 'all_off', 'block_forever'] },
}

/** Предупреждения сервера дословно (memory_ops.WARNINGS): с командами терминала, которые интерфейс не должен показывать. */
export const WARNINGS = {
  rule_disable: [
    { id: 'rule_off', text: 'правило перестанет применяться: новая версия rulebook без него (правило в испытании — испытание закрывается)' },
    { id: 'block_forever', text: 'условие правила уходит в чёрный список бессрочно: цикл не вернёт его в испытание; снять — ./tb rulebook unblock' },
  ],
  rules_disable_all: [
    { id: 'switch_off', text: 'применение всех правил выключается сразу; включить снова — ./tb rulebook apply on' },
    { id: 'all_off', text: 'все активные правила выключаются, испытания закрываются, новая версия rulebook без правил' },
    { id: 'block_forever', text: 'условие правила уходит в чёрный список бессрочно: цикл не вернёт его в испытание; снять — ./tb rulebook unblock' },
  ],
}

const env = (state = 'ok', reason = null) => ({ ok: true, state, reason })
const iso = (s) => new Date(s * 1000).toISOString()

export function casesDetail(now = Date.now() / 1000) {
  return {
    ...env(), total: 214, by_type: { trade: 61, skip: 140, idea: 13 }, by_status: { closed: 58, open: 3, na: 153 },
    modes: [{ mode: 'paper', n: 52, mean_r: num(-0.08, 52), win_share: num(0.42, 52) }, { mode: 'testnet', n: 6, mean_r: num(0.31, 6), win_share: num(0.5, 6) }],
    recent: [
      { case_id: 'C-20261103-0007', ts: iso(now - 1800), type: 'trade', mode: 'paper', sym: 'SOLUSDT', side: 'long', setup: 'trend_pullback', action: 'enter', status: 'closed', r: 1.2, rulebook_ver: 'RV3', eligible: true },
      { case_id: 'C-20261103-0006', ts: iso(now - 5400), type: 'skip', mode: 'paper', sym: 'ETHUSDT', side: 'short', setup: 'level_reject', action: 'skip', status: 'na', r: null, rulebook_ver: 'RV3', eligible: true },
      { case_id: 'C-20261103-0005', ts: iso(now - 9000), type: 'trade', mode: 'paper', sym: 'DOGEUSDT', side: 'long', setup: 'range_fade', action: 'enter', status: 'closed', r: -1, rulebook_ver: 'RV2', eligible: false },
    ],
    similar: { state: 'stage3', reason: 'появится на этапе 3' },
  }
}

const rule = (id, status, extra = {}) => ({
  rule_id: id, rev: 2, kind: 'skip_when', status, wait: null, reason_code: null, from_lesson: 'L-0003', ver_in: null, ver_out: null,
  cond_txt: 'спред bps >= 5', cond_h: 'h' + id, env_norm: 'paper', ts: iso(Date.now() / 1000 - D), in_version: false, ...extra,
})

export function lessonsDetail(now = Date.now() / 1000) {
  return {
    ...env(), db: 'ok', db_reason: null, total: 4, by_status: { hyp: 2, confirmed: 1, rejected: 1 }, by_src: { labels: 3, brain: 1 }, small_n: 30,
    rows: [
      {
        lesson_id: 'L-0004', src: 'labels', status: 'hyp', reason_code: 'HOLDOUT', txt: null, cond_txt: 'диапазон 5м % >= 5', claim: { metric: 'mean_r', dir: 'lower', vs: 'base' }, created_by: 'code',
        conf: null, n: 12, effect: eff(-0.62, -1.4, 0.16, 12), p: 0.08, ts: iso(now - 2 * D), expires_ts: null,
        trial: { trial_id: 'T-0004', rule_id: 'RL0004', state: 'open', verdict: null, start_ts: now - D, deadline_ts: now + 6 * D, closed_ts: null }, rule: rule('RL0004', 'trial'), can_reject: true,
      },
      {
        lesson_id: 'L-0003', src: 'labels', status: 'confirmed', reason_code: 'PROMOTE', txt: null, cond_txt: 'спред bps >= 5', claim: { metric: 'mean_r', dir: 'lower', vs: 'base' }, created_by: 'code',
        conf: null, n: 64, effect: eff(-0.41, -0.66, -0.16, 64), p: 0.002, ts: iso(now - 12 * D), expires_ts: null,
        trial: { trial_id: 'T-0003', rule_id: 'RL0001', state: 'closed', verdict: 'promote', start_ts: now - 10 * D, deadline_ts: now - 3 * D, closed_ts: now - 3 * D },
        rule: rule('RL0001', 'active', { ver_in: 'RV3', in_version: true }), can_reject: true,
      },
      {
        lesson_id: 'L-0002', src: 'brain', status: 'hyp', reason_code: null, txt: 'Пробой в первые 15 минут американской сессии чаще ложный — ждать закрепления.', cond_txt: 'сетап = breakout_retest',
        claim: { metric: 'win_rate', dir: 'lower', vs: 'base' }, created_by: 'reviewer', conf: 0.6, n: 41, effect: eff(-0.12, -0.25, 0.01, 41), p: 0.07, ts: iso(now - 5 * D),
        expires_ts: now + 9 * D, trial: null, rule: null, can_reject: false,
      },
      {
        lesson_id: 'L-0001', src: 'labels', status: 'rejected', reason_code: 'TRIAL_REJECT', txt: null, cond_txt: 'сессия = {asia}', claim: { metric: 'mean_r', dir: 'lower', vs: 'base' }, created_by: 'code',
        conf: null, n: 33, effect: eff(-0.2, -0.5, 0.1, 33), p: 0.2, ts: iso(now - 20 * D), expires_ts: null, trial: null, rule: null, can_reject: false,
      },
    ],
  }
}

export function rulesDetail(now = Date.now() / 1000) {
  return {
    ...env(), mode: { apply: true, why: 'OK', ver: 'RV3' }, current: 'RV3', versions_total: 3, counts: { active: 1, trial: 3, rejected: 1 },
    versions: [
      { ver: 'RV3', parent: 'RV2', reason_code: 'PROMOTE', rule_ids: ['RL0001'], trial_id: 'T-0003', ts: iso(now - 3 * D) },
      { ver: 'RV2', parent: 'RV1', reason_code: 'ROLLBACK', rule_ids: [], trial_id: null, ts: iso(now - 8 * D) },
      { ver: 'RV1', parent: null, reason_code: 'INIT', rule_ids: [], trial_id: null, ts: iso(now - 30 * D) },
    ],
    active: [rule('RL0001', 'active', { ver_in: 'RV3', in_version: true })],
    waiting: [
      { rule_id: 'RL0005', wait: 'READY', since_ts: now - 3600, waited_s: 3600, deadline_ts: null, left_s: null, from_lesson: 'L-0005', cond_txt: 'RR плана <= 1.5' },
      { rule_id: 'RL0006', wait: 'MAX_ACTIVE', since_ts: now - 2 * D, waited_s: 2 * D, deadline_ts: null, left_s: null, from_lesson: 'L-0006', cond_txt: 'сессия = {asia}' },
      { rule_id: 'RL0007', wait: 'LOSS_CAP', since_ts: now - D, waited_s: D, deadline_ts: now + 2 * D, left_s: 2 * D, from_lesson: 'L-0007', cond_txt: 'изменение 24ч % >= 12' },
    ],
    rules: [rule('RL0001', 'active', { ver_in: 'RV3', in_version: true }), rule('RL0004', 'trial')], rules_total: 5,
    feat_missing: { window_days: 7, all: num(0.04, 180), by_rule: [{ ...num(0.04, 180), rule_id: 'RL0001', missing: 7 }] },
    no_event: { ...num(0.25, 24), window_days: 7, threshold: 0.2, yellow: true },
  }
}

export function pathDetail(now = Date.now() / 1000) {
  const cand = (id, status, code, lesson_id) => ({ id, menu_id: 'M-2026-44', src: 'labels', status, code, p_screen: 0.01, holdout_start: now - 9 * D, holdout_end: now - 2 * D, ts: now - 10 * D, lesson_id })
  const strip = ({ lesson_id: _l, ...c }) => c
  return {
    ...env(), lesson_id: null, funnel: { candidates: 9, hyp: 3, trial: 2, confirmed: 1, rule_active: 1 },
    paths: [
      { candidate: strip(cand(3, 'hyp', null)), lesson: { lesson_id: 'L-0003', status: 'confirmed', txt: null },
        trial: { trial_id: 'T-0003', rule_id: 'RL0001', state: 'closed', verdict: 'promote', start_ts: now - 10 * D, deadline_ts: now - 3 * D, closed_ts: now - 3 * D },
        rule: rule('RL0001', 'active', { ver_in: 'RV3' }), version: { in: 'RV3', in_ts: iso(now - 3 * D), out: null, out_ts: null, out_reason: null } },
      { candidate: strip(cand(4, 'hyp', null)), lesson: { lesson_id: 'L-0004', status: 'hyp', txt: null },
        trial: { trial_id: 'T-0004', rule_id: 'RL0004', state: 'open', verdict: null, start_ts: now - D, deadline_ts: now + 6 * D, closed_ts: null }, rule: rule('RL0004', 'trial', { cond_txt: 'диапазон 5м % >= 5' }), version: null },
      { candidate: strip(cand(5, 'dropped', 'NOT_SIGNIFICANT')), lesson: null, trial: null, rule: null, version: null },
    ],
  }
}

/** Все четыре ответа «нет данных» (база без таблиц цикла), как у сервера. */
export const NO_DATA = {
  memory_cases: { ...env('no_data', 'нет данных: случаев пока нет'), total: 0, by_type: {}, by_status: {}, modes: [], recent: [], similar: { state: 'stage3', reason: 'появится на этапе 3' } },
  memory_lessons: { ...env('no_data', 'нет данных: уроков пока нет'), db: 'no_data', db_reason: 'нет данных: таблиц цикла нет', total: 0, by_status: {}, by_src: {}, small_n: 30, rows: [] },
  memory_rules: { ...env('no_data', 'нет данных: таблиц цикла нет'), mode: { apply: false, why: 'DB', ver: null } },
  memory_path: { ...env('no_data', 'нет данных: таблиц цикла нет'), funnel: null, paths: [] },
}

/** Ответы сервера на чтение: op -> data. over: подмена отдельных ответов. */
export function serveMemory(dash, over = {}) {
  const full = { memory_cases: casesDetail, memory_lessons: lessonsDetail, memory_rules: rulesDetail, memory_path: pathDetail }
  for (const op of Object.keys(full)) {
    dash.replies[op] = () => {
      const v = op in over ? over[op] : full[op]()
      if (v === 'silence') return null
      return { topic: `${op}_detail`, data: typeof v === 'function' ? v() : v }
    }
  }
}

/** Аварийные действия как у memory_ops.MemoryActions.handle: CSRF, challenge -> CONFIRM с warnings, команда только с accepted = все id. */
export function serveActions(dash, { result = {} } = {}) {
  for (const op of ['lesson_reject', 'rule_disable', 'rules_disable_all']) {
    dash.replies[op] = (m) => {
      const base = { id: m.id, op, msg: '' }
      if (m.csrf !== MEMORY_HELLO.csrf) return { topic: 'memory_action_result', data: { ...base, ok: false, code: 'E_CSRF', msg: 'сессия устарела: обновите страницу' } }
      const warns = WARNINGS[op] ?? []
      if (m.kind === 'challenge') return { topic: 'memory_action_result', data: { ...base, ok: true, code: warns.length ? 'CONFIRM' : 'OK', warnings: warns, needs_confirm: warns.length > 0 } }
      const ids = warns.map((w) => w.id).sort()
      const acc = Array.isArray(m.accepted) ? [...new Set(m.accepted)].sort() : []
      if (warns.length && JSON.stringify(acc) !== JSON.stringify(ids)) return { topic: 'memory_action_result', data: { ...base, ok: false, code: 'E_CONFIRM', warnings: warns, needs_confirm: true } }
      return { topic: 'memory_action_result', data: { ...base, ok: true, code: 'OK', msg: 'готово', report: { ok: true }, ...(result[op] ?? {}) } }
    }
  }
}
