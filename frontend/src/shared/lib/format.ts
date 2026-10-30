/** Возраст по метке времени: «только что», «12 с назад», «3 мин назад», «2 ч назад», «5 дн назад». now в секундах (epoch). */
export function ageText(tsSeconds: number | null | undefined, nowSeconds: number): string {
  if (typeof tsSeconds !== 'number' || !Number.isFinite(tsSeconds)) return '–'
  const s = Math.max(0, Math.floor(nowSeconds - tsSeconds))
  if (s < 5) return 'только что'
  if (s < 60) return `${s} с назад`
  if (s < 3600) return `${Math.floor(s / 60)} мин назад`
  if (s < 86400) return `${Math.floor(s / 3600)} ч назад`
  return `${Math.floor(s / 86400)} дн назад`
}

/** Число с фиксированным числом знаков; не число -> «–». */
export function fixed(x: number | null | undefined, digits = 2): string {
  return typeof x === 'number' && Number.isFinite(x) ? x.toFixed(digits) : '–'
}

/** Доллары: $0.0123 (до 4 знаков для малых сумм). */
export function usd(x: number | null | undefined): string {
  if (typeof x !== 'number' || !Number.isFinite(x)) return '–'
  return `$${x < 1 ? x.toFixed(4) : x.toFixed(2)}`
}
