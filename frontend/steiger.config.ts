import fsd from '@feature-sliced/steiger-plugin'
import { defineConfig } from 'steiger'

// Проверка архитектуры FSD (npm run lint:fsd): слои, импорты вниз, публичный API слайсов.
// insignificant-slice отключено на время переноса: пока на сайте одна страница, у каждой сущности и виджета ровно одно использование;
// правило вернём, когда экранов станет больше (этап C плана).
export default defineConfig([
  ...fsd.configs.recommended,
  { files: ['./src/**'], rules: { 'fsd/insignificant-slice': 'off' } },
])
