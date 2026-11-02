/**
 * Копирование текста в буфер обмена. Сначала современный способ (нужен защищённый контекст: localhost подходит), затем запасной через временное поле.
 * Возвращает true, если скопировано; false, если браузер не дал (тогда интерфейс просит выделить и скопировать самому).
 */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    // запрещено разрешениями страницы: пробуем запасной способ
  }
  try {
    const el = document.createElement('textarea')
    el.value = text
    el.setAttribute('readonly', '')
    el.style.cssText = 'position:fixed;top:0;left:0;opacity:0;pointer-events:none'
    document.body.appendChild(el)
    el.select()
    const ok = document.execCommand('copy')
    el.remove()
    return ok
  } catch {
    return false
  }
}
