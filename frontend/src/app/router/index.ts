import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { BriefPage } from '@/pages/brief'
import { DayPage } from '@/pages/day'
import { DecisionsPage } from '@/pages/decisions'
import { ExperimentsPage } from '@/pages/experiments'
import { HomePage } from '@/pages/home'
import { IdeasPage } from '@/pages/ideas'
import { LearningPage } from '@/pages/learning'
import { MarketPage } from '@/pages/market'
import { MemoryPage } from '@/pages/memory'
import { NewsPage } from '@/pages/news'
import { NotifyPage } from '@/pages/notify'
import { PlansPage } from '@/pages/plans'
import { SettingsPage } from '@/pages/settings'
import { SystemPage } from '@/pages/system'
import { TradesPage } from '@/pages/trades'

/** Старый адрес ведёт на новый с теми же параметрами (?plan=, ?coin= и т.д.): ссылки из заметок, истории браузера и чужих сообщений продолжают работать. */
const moved = (from: string, to: string): RouteRecordRaw => ({ path: from, redirect: (r) => ({ path: to, query: r.query, hash: r.hash }) })

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: HomePage, meta: { title: 'Обзор' } },
    { path: '/market', name: 'market', component: MarketPage, meta: { title: 'Рынок: карта' } },
    { path: '/market/news', name: 'news', component: NewsPage, meta: { title: 'Рынок: новости' } },
    { path: '/trading/ideas', name: 'ideas', component: IdeasPage, meta: { title: 'Торговля: идеи' } },
    { path: '/trading/plans', name: 'plans', component: PlansPage, meta: { title: 'Торговля: планы' } },
    { path: '/trading/trades', name: 'trades', component: TradesPage, meta: { title: 'Торговля: сделки' } },
    { path: '/trading/day', name: 'day', component: DayPage, meta: { title: 'Торговля: день' } },
    { path: '/trading/experiments', name: 'experiments', component: ExperimentsPage, meta: { title: 'Торговля: эксперименты' } },
    { path: '/brain/decisions', name: 'decisions', component: DecisionsPage, meta: { title: 'Мозг: решения' } },
    { path: '/brain/brief', name: 'brief', component: BriefPage, meta: { title: 'Мозг: досье' } },
    { path: '/brain/learning', name: 'learning', component: LearningPage, meta: { title: 'Мозг: учится ли' } },
    { path: '/brain/memory', name: 'memory', component: MemoryPage, meta: { title: 'Мозг: память' } },
    { path: '/system/control', name: 'control', component: SystemPage, meta: { title: 'Система: управление' } },
    { path: '/system/settings', name: 'settings', component: SettingsPage, meta: { title: 'Система: настройки' } },
    { path: '/system/notify', name: 'notify', component: NotifyPage, meta: { title: 'Система: уведомления' } },
    moved('/system', '/system/control'),
    moved('/notify', '/system/notify'),
    moved('/settings', '/system/settings'),
    moved('/control', '/system/control'),
    moved('/trading', '/trading/trades'),
    moved('/brain', '/brain/decisions'),
    moved('/ideas', '/trading/ideas'),
    moved('/plans', '/trading/plans'),
    moved('/trades', '/trading/trades'),
    moved('/day', '/trading/day'),
    moved('/decisions', '/brain/decisions'),
    moved('/brief', '/brain/brief'),
  ],
})
