/* Ранняя установка темы до первого рендера (без вспышки). Обычный скрипт, не модуль: CSP сервера запрещает встроенные скрипты, а этот файл свой.
   Ключ и правило те же, что в src/shared/lib/theme/theme.ts: только 'light' и 'dark' ставят data-theme, «как в системе» его не ставит. */
try {
  var t = localStorage.getItem('tb.theme')
  if (t === 'light' || t === 'dark') document.documentElement.setAttribute('data-theme', t)
} catch (e) { /* хранилище недоступно: тема по системе */ }
