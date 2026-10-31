import { createRouter, createWebHistory } from 'vue-router'
import { HomePage } from '@/pages/home'
import { DecisionsPage } from '@/pages/decisions'
import { IdeasPage } from '@/pages/ideas'
import { PlansPage } from '@/pages/plans'
import { TradesPage } from '@/pages/trades'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: HomePage, meta: { title: 'Обзор' } },
    { path: '/decisions', name: 'decisions', component: DecisionsPage, meta: { title: 'Решения' } },
    { path: '/ideas', name: 'ideas', component: IdeasPage, meta: { title: 'Идеи' } },
    { path: '/plans', name: 'plans', component: PlansPage, meta: { title: 'Планы' } },
    { path: '/trades', name: 'trades', component: TradesPage, meta: { title: 'Сделки' } },
  ],
})
