/**
 * Разделы платформы для боковой колонки и полосы вкладок. Пункт меню либо ведёт на одну страницу, либо объединяет несколько страниц-вкладок (tabs): вкладки показывает полоса под шапкой,
 * в колонке остаётся один пункт. Новый экран: запись во вкладки нужного пункта и маршрут в app/router. Значок: путь SVG 24×24 (контур, линия 1.8).
 */
export type NavZone = 'now' | 'brain' | 'trades' | 'market' | 'system'

export interface NavTab {
  to: string
  label: string
  /** ключ счётчика «ждёт вашего ответа» (значение считает приложение, например 'day'); без ключа метки нет */
  badge?: string
}

export interface NavItem {
  to: string
  label: string
  icon: string
  /** цвет раздела (значок и выделение): как зоны старого дашборда */
  zone: NavZone
  /** префикс пути, по которому пункт считается активным (у пунктов с вкладками); без него пункт активен только на своём пути */
  base?: string
  tabs?: NavTab[]
  /** ключ счётчика на пункте: метка видна, если на любой его вкладке что-то ждёт ответа */
  badge?: string
}

export interface NavGroup {
  id: string
  label: string
  items: NavItem[]
}

export const NAV_GROUPS: NavGroup[] = [
  {
    id: 'trade',
    label: 'Торговля',
    items: [
      { to: '/', label: 'Обзор', icon: 'M3 11l9-8 9 8M5 9.5V20h5v-6h4v6h5V9.5', zone: 'now' },
      { to: '/market', label: 'Рынок', icon: 'M3 17l5-6 4 4 8-9M15 6h5v5', zone: 'market', base: '/market', tabs: [{ to: '/market', label: 'Карта' }, { to: '/market/news', label: 'Новости' }] },
      {
        to: '/trading/trades', label: 'Торговля', icon: 'M5 4h14v16H5zM9 9h6M9 13h6M9 17h3', zone: 'trades', base: '/trading', badge: 'day',
        tabs: [{ to: '/trading/trades', label: 'Сделки' }, { to: '/trading/plans', label: 'Планы' }, { to: '/trading/ideas', label: 'Идеи' }, { to: '/trading/day', label: 'День', badge: 'day' }, { to: '/trading/experiments', label: 'Эксперименты' }],
      },
      { to: '/brain/decisions', label: 'Мозг', icon: 'M4 6h16M4 12h10M4 18h6M17 14l2.5 2.5L23 12', zone: 'brain', base: '/brain', tabs: [{ to: '/brain/decisions', label: 'Решения' }, { to: '/brain/brief', label: 'Досье' }, { to: '/brain/learning', label: 'Учится ли мозг' }, { to: '/brain/memory', label: 'Память' }] },
      { to: '/system/control', label: 'Система', icon: 'M12 3v9m5.7-6.7a8 8 0 1 1-11.4 0', zone: 'system', base: '/system', tabs: [{ to: '/system/control', label: 'Управление' }, { to: '/system/settings', label: 'Настройки' }, { to: '/system/notify', label: 'Уведомления' }] },
    ],
  },
]

/** Активен ли пункт на этом пути: по префиксу (пункты с вкладками) или по точному совпадению. */
export function isActive(item: NavItem, path: string): boolean {
  return item.base ? path === item.base || path.startsWith(item.base + '/') : path === item.to
}

/** Пункт с вкладками для текущего пути (для полосы вкладок) или null. */
export function sectionOf(path: string): NavItem | null {
  for (const g of NAV_GROUPS) for (const it of g.items) if (it.tabs && isActive(it, path)) return it
  return null
}
