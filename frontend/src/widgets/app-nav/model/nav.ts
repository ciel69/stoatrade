/** Разделы платформы для боковой колонки. Новый экран добавляется одной записью: путь должен быть в роутере (app/router). Значок: путь SVG 24×24 (контур, линия 1.8). */
export type NavZone = 'now' | 'brain' | 'trades' | 'market' | 'system'

export interface NavItem {
  to: string
  label: string
  icon: string
  /** цвет раздела (значок и выделение): как зоны старого дашборда */
  zone: NavZone
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
      { to: '/decisions', label: 'Решения', icon: 'M4 6h16M4 12h10M4 18h6M17 14l2.5 2.5L23 12', zone: 'brain' },
      { to: '/ideas', label: 'Идеи', icon: 'M9 18h6M10 21h4M12 3a6 6 0 0 0-3.6 10.8c.6.5 1.1 1.2 1.1 2.2h5c0-1 .5-1.7 1.1-2.2A6 6 0 0 0 12 3z', zone: 'brain' },
      { to: '/plans', label: 'Планы', icon: 'M5 4h14v16H5zM9 9h6M9 13h6M9 17h3', zone: 'trades' },
      { to: '/trades', label: 'Сделки', icon: 'M4 20V10m6 10V4m6 16v-7m4 7H2', zone: 'trades' },
    ],
  },
]
