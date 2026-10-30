import { REGION_COUNT } from '@/shared/ui'

/** Известные роли закреплены за областями, чтобы область роли не менялась от порядка в снимке. Новые роли занимают свободные области по алфавиту. */
const FIXED: Record<string, number> = { entry_scout: 0, position_manager: 1, news_reader: 2 }

const LABEL: Record<string, string> = { entry_scout: 'поиск входа', position_manager: 'сопровождение', news_reader: 'новости', morning_analyst: 'утренний обзор', day_auditor: 'аудит дня', reviewer: 'разбор сделок' }

export function roleLabel(role: string): string {
  return LABEL[role] ?? role
}

/** Область коры для каждой роли. Ролей может быть больше областей: тогда области делятся несколькими ролями. */
export function assignRegions(roles: string[]): Record<string, number> {
  const out: Record<string, number> = {}
  const rest: string[] = []
  for (const r of [...new Set(roles)].sort()) {
    if (r in FIXED) out[r] = FIXED[r]
    else rest.push(r)
  }
  const free = Array.from({ length: REGION_COUNT }, (_, i) => i).filter((i) => !Object.values(out).includes(i))
  rest.forEach((r, i) => { out[r] = free.length ? free[i % free.length] : i % REGION_COUNT })
  return out
}

/** Роли, живущие в области. */
export function rolesOfRegion(map: Record<string, number>, region: number): string[] {
  return Object.keys(map).filter((r) => map[r] === region)
}
