import { createRouter, createWebHistory } from 'vue-router'
import { HomePage } from '@/pages/home'
import { DecisionsPage } from '@/pages/decisions'
import { TradesPage } from '@/pages/trades'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: HomePage, meta: { title: 'Обзор' } },
    { path: '/decisions', name: 'decisions', component: DecisionsPage, meta: { title: 'Решения' } },
    { path: '/trades', name: 'trades', component: TradesPage, meta: { title: 'Сделки' } },
  ],
})
