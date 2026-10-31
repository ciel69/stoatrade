import type { RoleModel, RoleRow, RolesSnapshot } from '../model/types'

/** Человеческое название роли и что она делает: в данных только идентификатор. */
export const ROLE_INFO: Record<string, { label: string; what: string }> = {
  entry_scout: { label: 'Поиск входа', what: 'Смотрит события сканера и решает, готовить ли вход: ставит план или пропускает монету.' },
  morning_analyst: { label: 'Утренний обзор', what: 'Разбирает рынок перед днём и строит план дня: режим, квоту новых планов, на что смотреть.' },
  position_manager: { label: 'Сопровождение позиции', what: 'Ведёт открытую сделку: двигает стоп, фиксирует прибыль, решает о выходе.' },
  day_auditor: { label: 'Аудит дня', what: 'Проверяет итог дня и соблюдение правил.' },
  news_reader: { label: 'Читатель новостей', what: 'Размечает заголовки в карантине: тип, важность, направление, монеты. Мозгу отдаёт только метки.' },
  reviewer: { label: 'Разбор сделок', what: 'Разбирает закрытые сделки и пишет уроки.' },
}
export const roleLabel = (r: string): string => ROLE_INFO[r]?.label ?? r

/** Где работает модель: локальный провайдер (LM Studio) или облако. */
export const placeOf = (m: RoleModel | null): string => (!m ? '–' : m.provider === 'local' ? 'локально' : 'облако')

export interface RoleIssue {
  tone: 'warn' | 'bad'
  text: string
}

/** Что с ролью не так: выключенный провайдер основной или запасной модели, много неверных форматов и ошибок за сутки, роль для live без запасной модели при работе в live. */
export function issuesOf(r: RoleRow, mode: string | null): RoleIssue[] {
  const out: RoleIssue[] = []
  if (r.primary && !r.primary.provider_enabled) out.push({ tone: 'bad', text: `основная модель ${r.primary.model_name}: провайдер выключен, роль не отвечает` })
  for (const f of r.fallback) if (!f.provider_enabled) out.push({ tone: 'warn', text: `запасная модель ${f.model_name} не сработает: провайдер ${f.provider} выключен` })
  const calls = r.day.calls ?? 0
  if (calls >= 20 && (r.day.fmt_err_rate ?? 0) >= 0.15) out.push({ tone: 'warn', text: `${Math.round((r.day.fmt_err_rate ?? 0) * 100)}% ответов за сутки не приняты по формату` })
  if (calls >= 20 && (r.day.err_rate ?? 0) >= 0.15) out.push({ tone: 'warn', text: `${Math.round((r.day.err_rate ?? 0) * 100)}% вызовов за сутки закончились ошибкой` })
  if (mode === 'live' && r.live_risk && !r.fallback.length) out.push({ tone: 'warn', text: 'роль влияет на live, а запасной модели нет' })
  if (mode === 'live' && r.live_risk && r.primary && !r.primary.live_ok) out.push({ tone: 'bad', text: 'основная модель не допущена к live' })
  return out
}

/** Сводка вверху: какие модели думают и сколько ролей. */
export function summary(s: RolesSnapshot): { line: string; detail: string } {
  const names = [...new Set(s.roles.map((r) => r.primary?.model_name).filter((x): x is string => !!x))]
  const withFallback = s.roles.filter((r) => r.fallback.length).length
  const n = s.roles.length
  const word = n % 10 === 1 && n % 100 !== 11 ? 'роль' : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 12 || n % 100 > 14) ? 'роли' : 'ролей'
  const line = !n ? 'Роли мозга не загружены' : names.length === 1 ? `${n === 1 ? 'Единственная роль думает' : `Все ${n} ${word} думают`} одной моделью: ${names[0]}` : `${n} ${word}, моделей: ${names.length} (${names.join(', ')})`
  const detail = !n ? 'Нет данных от настроек мозга (config/agents.json, config/providers.json).' : `Запасная модель есть у ${withFallback} из ${n} ролей.`
  return { line, detail }
}

/** Сначала роли команды (влияют на сделки), затем общие. */
export function groups(s: RolesSnapshot): Array<{ id: 'team' | 'shared'; title: string; roles: RoleRow[] }> {
  const team = s.roles.filter((r) => r.scope === 'team')
  const shared = s.roles.filter((r) => r.scope !== 'team')
  return [
    ...(team.length ? [{ id: 'team' as const, title: `Команда${s.live_team ? ' ' + s.live_team : ''}: решения о сделках`, roles: team }] : []),
    ...(shared.length ? [{ id: 'shared' as const, title: 'Общие: новости, разбор, аудит', roles: shared }] : []),
  ]
}

export const pct = (x: number | null | undefined): string => (x == null ? '–' : `${Math.round(x * 100)}%`)
