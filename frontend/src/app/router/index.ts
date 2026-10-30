import { createRouter, createWebHistory } from 'vue-router'
import { HomePage } from '@/pages/home'
import { TradesPage } from '@/pages/trades'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: HomePage, meta: { title: 'Обзор' } },
    { path: '/trades', name: 'trades', component: TradesPage, meta: { title: 'Сделки' } },
  ],
})
