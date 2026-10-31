import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { BriefPage } from '@/pages/brief'
import { DecisionsPage } from '@/pages/decisions'
import { HomePage } from '@/pages/home'
import { IdeasPage } from '@/pages/ideas'
import { MarketPage } from '@/pages/market'
import { NewsPage } from '@/pages/news'
import { PlansPage } from '@/pages/plans'
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
    { path: '/brain/decisions', name: 'decisions', component: DecisionsPage, meta: { title: 'Мозг: решения' } },
    { path: '/brain/brief', name: 'brief', component: BriefPage, meta: { title: 'Мозг: досье' } },
    { path: '/system/control', name: 'control', component: SystemPage, meta: { title: 'Система: управление' } },
    moved('/system', '/system/control'),
    moved('/control', '/system/control'),
    moved('/trading', '/trading/ideas'),
    moved('/brain', '/brain/decisions'),
    moved('/ideas', '/trading/ideas'),
    moved('/plans', '/trading/plans'),
    moved('/trades', '/trading/trades'),
    moved('/decisions', '/brain/decisions'),
    moved('/brief', '/brain/brief'),
  ],
})
