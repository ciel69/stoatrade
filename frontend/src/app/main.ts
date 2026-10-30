import { createPinia } from 'pinia'
import { createApp } from 'vue'
import App from './App.vue'
import { router } from './router'
import { useLive } from '@/shared/api'
import '@/shared/ui/tokens.css'
import '@/shared/ui/motion.css'
import './styles/global.css'

createApp(App).use(createPinia()).use(router).mount('#app')

// единственный сокет приложения: подписки тем регистрируют виджеты, связь поднимается здесь
useLive().socket.start()
