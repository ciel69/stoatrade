import { modeName } from '@/shared/lib'

/** Режим платформы целиком (действие mode.set). Порядок как в ctl (SAFER_ORDER): чем больше, тем рискованнее. Переключать можно только на более безопасный. */
export const MODE_ORDER: Readonly<Record<string, number>> = { observe: 0, paper: 1, testnet: 2, live: 3 }

const EFFECT: Record<string, string> = {
  observe: 'Платформа только наблюдает: новые сделки не открываются, открытые позиции остаются под присмотром.',
  paper: 'Сделки станут виртуальными: реальные деньги и настоящая биржа не задействуются.',
  testnet: 'Сделки пойдут на тестовой бирже: она настоящая, но деньги там не настоящие.',
}

/** Режимы строго безопаснее текущего (в порядке от самого безопасного). */
export function saferModes(current: string | null | undefined): string[] {
  const cur = current != null ? MODE_ORDER[current] : undefined
  if (cur === undefined) return []
  return Object.keys(MODE_ORDER).filter((m) => MODE_ORDER[m] < cur)
}

/** Почему режим сейчас не сменить (текстом для человека), или null, если можно. */
export function modeBlock(state: string | null | undefined, current: string | null | undefined): string | null {
  if (state !== 'running') return 'Режим платформы меняется, пока идёт торговый день. Сейчас день не идёт: режим выбирается при его запуске.'
  if (!saferModes(current).length) return current === 'observe' ? 'Платформа уже в самом безопасном режиме: ниже только остановка дня.' : 'Режим неизвестен или более безопасных режимов нет.'
  return null
}

export const modeArgs = (mode: string): { mode: string } => ({ mode })

export const modeTitle = (to: string): string => `Режим платформы: ${modeName(to)}`

/** «Было → станет» одной строкой. */
export const modeLine = (from: string | null | undefined, to: string): string => `${modeName(from)} → ${modeName(to)}`

/** Что изменится: смысл режима и ограничение, которое действует всегда. */
export function modeEffect(to: string): string {
  return `${EFFECT[to] ?? 'Режим платформы изменится.'} Переключиться обратно на более рискованный режим отсюда нельзя: для этого останавливают день и запускают новый. Для торговых режимов не должно быть открытых позиций.`
}

export const modeReflects = (current: string | null | undefined, to: string): boolean => current === to
