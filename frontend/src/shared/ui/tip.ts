/**
 * Содержимое и геометрия всплывающей подсказки UiTip. Чистые функции без DOM, чтобы их можно было проверить тестами.
 */
export interface TipContent {
  /** короткий заголовок жирным */
  title?: string
  /** 1-2 предложения человеческим языком */
  text?: string
  /** пары «подпись: значение» под текстом (сетап, риск, срок плана) */
  rows?: ReadonlyArray<{ label: string; value: string }>
}

export interface Box {
  left: number
  top: number
  width: number
  height: number
}

export interface Placement {
  x: number
  y: number
  side: 'above' | 'below'
}

const clamp = (v: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, v))

/**
 * Где поставить подсказку размера size относительно элемента anchor в окне vw x vh: по центру, но не ближе margin к краю окна.
 * Предпочтительно ниже элемента; не помещается ниже и помещается выше, тогда выше.
 */
export function placeFloating(anchor: Box, size: { width: number; height: number }, vw: number, vh: number, gap = 8, margin = 8): Placement {
  const max = vw - size.width - margin
  const x = max < margin ? margin : clamp(anchor.left + anchor.width / 2 - size.width / 2, margin, max)
  const below = anchor.top + anchor.height + gap
  const fitsBelow = below + size.height <= vh - margin
  const fitsAbove = anchor.top - gap - size.height >= margin
  const side = fitsBelow || !fitsAbove ? 'below' : 'above'
  return { x, y: side === 'below' ? below : anchor.top - gap - size.height, side }
}

/** Есть ли что показывать. */
export const hasTip = (t: TipContent | null | undefined): t is TipContent => !!t && !!(t.title || t.text || t.rows?.length)
