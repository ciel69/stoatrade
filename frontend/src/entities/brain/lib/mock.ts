import type { BrainSnapshot, BrainStatus, DecisionRow, RoleRow } from '../model/types'

/** Пример снимка мозга для отладки без сервера (?brain=mock | mock-think | mock-down | mock-protect). Время считается от nowS, чтобы возрасты были живыми. */
export type BrainMock = 'idle' | 'thinking' | 'down' | 'protect'

const ref = (id: string) => ({ id, provider: 'deepseek', model: id, enabled: true, missing: false })
const role = (name: string, o: Partial<RoleRow>): RoleRow => ({ role: name, primary: ref('ds-chat'), fallback: [], last: null, today: { usd: 0, calls: 0, errors: 0, budget_left_usd: 1 }, live_risk: false, ...o })

export function mockSnapshot(kind: BrainMock, nowS: number): BrainSnapshot {
  const status: BrainStatus = kind
  const d = (seq: number, ago: number, o: Partial<DecisionRow>): DecisionRow => ({
    seq, t: nowS - ago, ts: '', what: 'brain_decision', kind: 'note', role: 'entry_scout', profile: 'ds-chat', plan: null, sym: 'SOL', decision: null, tool: null, reason: null, code: null, ok: true, text: null, ...o,
  })
  const down = kind === 'down'
  const decisions = down ? [] : [
    d(5, 12, { decision: 'propose', text: 'Вход в лонг от 144.0: цена вернулась за уровень, setup level_reclaim, стоп за минимум отката, до цели 2.4R.' }),
    d(4, 35, { sym: 'ETH', decision: 'skip', text: 'Пропуск: до ближайшего уровня больше трёх процентов, входить рано.' }),
    d(3, 55, { role: 'position_manager', sym: 'BTC', decision: 'move_stop', text: 'Стоп подтянут к цене входа после +1R: позиция больше не может уйти в убыток.' }),
    d(2, 75, { what: 'brain_why', sym: 'SOL', text: 'Держу позицию: структура не сломана, стоп стоит на бирже. Причина: HOLD_THESIS_VALID.' }),
  ]
  const roles: RoleRow[] = [
    role('entry_scout', { last: { profile: 'ds-chat', provider: 'deepseek', ok: !down, err: null, lat_ms: 900, t: nowS - 130, fallback_from: null }, today: { usd: 0.0214, calls: 9, errors: 0, budget_left_usd: 0.9 } }),
    role('position_manager', { last: { profile: 'ds-chat', provider: 'deepseek', ok: true, err: null, lat_ms: 1200, t: nowS - 300, fallback_from: 'lm-oss' }, today: { usd: 0.0063, calls: 3, errors: 1, budget_left_usd: 0.9 } }),
    role('news_reader', { last: { profile: 'lm-qwen', provider: 'local', ok: false, err: 'timeout', lat_ms: null, t: nowS - 3000, fallback_from: null }, primary: ref('lm-qwen'), today: { usd: 0, calls: 14, errors: 2, budget_left_usd: null } }),
    role('morning_analyst', {}),
    role('day_auditor', {}),
    role('reviewer', {}),
  ]
  return {
    status, alive: !down, protect_reason: kind === 'protect' ? 'Дневной лимит убытка достигнут.' : null, health: 'green', rev: 1, inflight: kind === 'thinking' ? 1 : 0, queue: { new: kind === 'thinking' ? 2 : 0 }, cooling: [], budget: null, errors: 0,
    active: down ? null : { profile: 'ds-chat', provider: 'deepseek', model: 'ds-chat', role: 'entry_scout', ok: true, t: nowS - 130 }, roles, decisions,
  }
}
