/** Чистая логика модального окна (UiModal): какие элементы получают фокус и куда он переходит по Tab, чтобы не уйти из окна. */

export const FOCUSABLE = 'button:not([disabled]), a[href], input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])'

/** Элементы окна, на которые можно перейти с клавиатуры (видимые, не в закрытом details). */
export function focusables(root: HTMLElement): HTMLElement[] {
  return [...root.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((e) => {
    if (e.closest('[inert]')) return false
    const d = e.closest('details')
    if (d && !d.open && e.tagName !== 'SUMMARY') return false
    return e.getClientRects().length > 0
  })
}

/**
 * Куда перевести фокус по Tab, чтобы он не вышел за окно: с последнего вперёд на первый, с первого (или с самой рамки) назад на последний.
 * null: браузер сам переведёт фокус внутри окна.
 */
export function wrapTarget<T>(els: readonly T[], active: unknown, root: unknown, shift: boolean): T | null {
  if (!els.length) return null
  const first = els[0]
  const last = els[els.length - 1]
  if (shift && (active === first || active === root || !els.includes(active as T))) return last
  if (!shift && (active === last || !els.includes(active as T))) return first
  return null
}
