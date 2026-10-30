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

/** Число со знаком: +1.20, −0.35 (настоящий минус), 0.00. */
export function signed(x: number | null | undefined, digits = 2): string {
  if (typeof x !== 'number' || !Number.isFinite(x)) return '–'
  const s = Math.abs(x).toFixed(digits)
  return x > 0 && Number(s) !== 0 ? `+${s}` : x < 0 && Number(s) !== 0 ? `−${s}` : s
}

/** Цена по величине: значащие цифры, а не фиксированные знаки (0.004604, 25.82, 2224.4). */
export function price(x: number | null | undefined): string {
  if (typeof x !== 'number' || !Number.isFinite(x)) return '–'
  const a = Math.abs(x)
  if (a === 0) return '0'
  if (a >= 1000) return x.toFixed(1)
  if (a >= 100) return x.toFixed(2)
  if (a >= 1) return x.toFixed(3)
  if (a >= 0.01) return x.toFixed(4)
  return x.toPrecision(4)
}

/** Доллары: $0.0123 (до 4 знаков для малых сумм). */
export function usd(x: number | null | undefined): string {
  if (typeof x !== 'number' || !Number.isFinite(x)) return '–'
  return `$${x < 1 ? x.toFixed(4) : x.toFixed(2)}`
}

/** Длительность: 45 с, 12 мин, 2 ч 10 мин. */
export function duration(seconds: number | null | undefined): string {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds)) return '–'
  const s = Math.max(0, Math.round(seconds))
  if (s < 60) return `${s} с`
  if (s < 3600) return `${Math.round(s / 60)} мин`
  const h = Math.floor(s / 3600)
  const m = Math.round((s % 3600) / 60)
  return m ? `${h} ч ${m} мин` : `${h} ч`
}
