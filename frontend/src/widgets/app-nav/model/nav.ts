/** Разделы платформы для боковой колонки. Новый экран добавляется одной записью: путь должен быть в роутере (app/router). Значок: путь SVG 24×24 (контур, линия 1.8). */
export interface NavItem {
  to: string
  label: string
  icon: string
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
      { to: '/', label: 'Обзор', icon: 'M3 11l9-8 9 8M5 9.5V20h5v-6h4v6h5V9.5' },
      { to: '/decisions', label: 'Решения', icon: 'M4 6h16M4 12h10M4 18h6M17 14l2.5 2.5L23 12' },
      { to: '/trades', label: 'Сделки', icon: 'M4 20V10m6 10V4m6 16v-7m4 7H2' },
    ],
  },
]
