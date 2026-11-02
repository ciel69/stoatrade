import type { ExchangeRow } from '../model/types'
import { modeText } from './connect'

/** Правка биржи (действие exchange.set): сделать основной (activate) и сменить режим биржи (mode). Меняются только при остановленной платформе. */

export const EXCHANGE_MODES: ReadonlyArray<{ value: string; label: string; hint: string }> = [
  { value: 'paper', label: 'Бумага', hint: 'Виртуальные сделки, реальных денег нет.' },
  { value: 'testnet', label: 'Тестнет', hint: 'Настоящая биржа, но деньги не настоящие.' },
  { value: 'live', label: 'Живые деньги', hint: 'Сделки на настоящие деньги.' },
]

const KEY_FOR: Record<string, { kind: string; word: string }> = { testnet: { kind: 'testnet', word: 'тестовой сети' }, live: { kind: 'trade', word: 'торговли' } }

/** Почему биржи сейчас не менять: платформа должна быть остановлена. */
export function exchangeBlock(platformState: string | null | undefined): string | null {
  if (platformState === 'stopped') return null
  if (platformState == null) return 'Состояние платформы неизвестно: биржи можно менять, только когда платформа остановлена.'
  return 'Биржи меняются, только когда платформа остановлена (день не идёт). Завершите торговый день, затем меняйте.'
}

/** Почему эту биржу нельзя сделать основной, или null. */
export function activateBlock(e: ExchangeRow): string | null {
  if (e.active) return 'Эта биржа уже основная.'
  if (!e.implemented) return 'Для этой биржи адаптер ещё не написан: торговать на ней нельзя.'
  if (!e.allowed) return 'Биржа не разрешена владельцем: сначала её нужно разрешить в настройках платформы.'
  return null
}

export const activateArgs = (e: Pick<ExchangeRow, 'id'>): { op: 'activate'; id: string } => ({ op: 'activate', id: e.id })
export const exchangeModeArgs = (e: Pick<ExchangeRow, 'id'>, mode: string): { op: 'mode'; id: string; mode: string } => ({ op: 'mode', id: e.id, mode })

/** Режим можно выбрать: он отличается от текущего. */
export const canSetMode = (e: ExchangeRow, mode: string | null): mode is string => !!mode && mode !== e.mode && EXCHANGE_MODES.some((m) => m.value === mode)

/** Не хватает ключа нужного вида (для тестнета и live): предупреждение до применения, решение за сервером. */
export function keyNote(e: ExchangeRow, mode: string): string | null {
  const need = KEY_FOR[mode]
  if (!need || e.creds.includes(need.kind)) return null
  return `Ключа для ${need.word} у этой биржи нет: торговать в этом режиме платформа не сможет, пока владелец его не добавит.`
}

export const activateTitle = (e: ExchangeRow): string => `Основная биржа: ${e.name}`
export const exchangeModeTitle = (e: ExchangeRow, mode: string): string => `Режим биржи ${e.name}: ${modeText(mode)}`

/** «Было → станет» для смены основной биржи. */
export function activateLine(from: ExchangeRow | null | undefined, to: ExchangeRow): string {
  return `${from ? from.name : 'основной биржи не было'} → ${to.name}`
}

export function activateEffect(from: ExchangeRow | null | undefined, to: ExchangeRow): string {
  const modeWord = to.mode === 'live' ? ' Эта биржа в режиме «живые деньги»: подтверждение будет усиленным.' : ''
  return `Платформа будет торговать на бирже ${to.name} (режим: ${modeText(to.mode)})${from ? `, а не на ${from.name}` : ''}. Действует со следующего запуска дня.${modeWord}`
}

export const exchangeModeLine = (e: ExchangeRow, mode: string): string => `${modeText(e.mode)} → ${modeText(mode)}`

export function exchangeModeEffect(e: ExchangeRow, mode: string): string {
  if (mode === 'live') return `Биржа ${e.name} начнёт торговать на реальные деньги со следующего запуска дня. Это самое рискованное состояние: потребуется явное подтверждение, и за потери отвечает только владелец.`
  const base = EXCHANGE_MODES.find((m) => m.value === mode)?.hint ?? ''
  return `Биржа ${e.name} перейдёт в режим «${modeText(mode)}» со следующего запуска дня. ${base}`.trim()
}

/** Тема отразила изменение. */
export function exchangeReflects(e: ExchangeRow | null | undefined, exp: { active?: true; mode?: string }): boolean {
  if (!e) return false
  if (exp.active && !e.active) return false
  if (exp.mode !== undefined && e.mode !== exp.mode) return false
  return true
}
