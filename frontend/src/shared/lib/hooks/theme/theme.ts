/**
 * Тема оформления: три выбора. «Как в системе» (атрибута data-theme нет, работает prefers-color-scheme), «Светлая» и «Тёмная» (атрибут на :root).
 * Выбор запоминается в localStorage; без хранилища (приватное окно, запрет) всё работает, просто не запоминается.
 * Тот же ключ и то же правило применения продублированы в public/theme-init.js: он выполняется до первого рендера, чтобы не было вспышки не той темы.
 */
export type ThemePref = 'system' | 'light' | 'dark'

export const THEME_KEY = 'tb.theme'
export const THEME_PREFS: ReadonlyArray<{ value: ThemePref; label: string }> = [
  { value: 'system', label: 'Как в системе' },
  { value: 'light', label: 'Светлая' },
  { value: 'dark', label: 'Тёмная' },
]

/** Любое сохранённое значение в один из трёх выборов; мусор и пусто: «как в системе». */
export function parseTheme(raw: unknown): ThemePref {
  return raw === 'light' || raw === 'dark' ? raw : 'system'
}

function store(): Storage | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage
  } catch {
    return null
  }
}

export function readTheme(storage: Pick<Storage, 'getItem'> | null = store()): ThemePref {
  try {
    return parseTheme(storage?.getItem(THEME_KEY))
  } catch {
    return 'system'
  }
}

/** Запомнить выбор; «как в системе» это отсутствие записи. false: хранилище недоступно. */
export function saveTheme(pref: ThemePref, storage: Pick<Storage, 'setItem' | 'removeItem'> | null = store()): boolean {
  try {
    if (!storage) return false
    if (pref === 'system') storage.removeItem(THEME_KEY)
    else storage.setItem(THEME_KEY, pref)
    return true
  } catch {
    return false
  }
}

/** Поставить атрибут data-theme на корень; при «как в системе» снять его. */
export function applyTheme(pref: ThemePref, root: Pick<HTMLElement, 'setAttribute' | 'removeAttribute'> = document.documentElement): void {
  if (pref === 'system') root.removeAttribute('data-theme')
  else root.setAttribute('data-theme', pref)
}
