import type { BrainStatus } from '../model/types'

/**
 * Настроение мозга: короткие шутливые реплики для облака над мозгом. Это не отчёт о торговле (никаких сделок, денег и прогнозов), а характер:
 * настоящие решения мозга живут отдельно, в списке «Последние мысли». Словарь целиком на клиенте, ничего не запрашивается.
 * Для «не отвечает» облака нет.
 */
export const MOOD: Record<Exclude<BrainStatus, 'down'>, readonly string[]> = {
  thinking: [
    'взвешиваю риски…', 'а что скажет график?', 'считаю, не торопясь', 'кофе бы, но я мозг', 'перебираю варианты…',
    'нейроны на низком старте', 'так, без паники…', 'сверяюсь с правилами', 'ищу, где подвох', 'секунду, думаю красиво',
    'складываю пазл', 'спокойно, я на связи',
  ],
  idle: [
    'зеваю нейроном…', 'подремлю чуток', 'тишина… люблю', 'считаю овец, по одной', 'режим энергосбережения',
    'отдыхаю, но одним глазом', 'мечтаю о длинном отпуске',
  ],
  protect: [
    'аккуратно, без суеты', 'семь раз отмерю…', 'держусь настороже', 'сначала безопасность', 'тихо иду, смотрю под ноги', 'не спеша, по правилам',
  ],
}

/** Случайная реплика для состояния, не равная прошлой; для «не отвечает» и неизвестного состояния null. */
export function pickQuip(status: BrainStatus, last: string | null, rnd: () => number = Math.random): string | null {
  if (status === 'down') return null
  const list = MOOD[status]
  if (!list?.length) return null
  const pool = list.filter((q) => q !== last)
  return pool[Math.floor(rnd() * pool.length)]
}

/** Пауза до следующей реплики, мс: думает меняет чаще всего (4-6 с), защита реже; у отдыха реплика показывается 5 с, потом облако прячется на 9-15 с. */
export function quipDelay(status: BrainStatus, rnd: () => number = Math.random): number {
  if (status === 'thinking') return 4000 + rnd() * 2000
  if (status === 'protect') return 6000 + rnd() * 2000
  return 5000
}
export function quietDelay(rnd: () => number = Math.random): number {
  return 9000 + rnd() * 6000
}
