import type { NotifyCategory, NotifySettings, NotifySnapshot } from '../model/types'

export type NotifyMock = 'mock' | 'mock-quiet' | 'mock-empty'

const CATS: ReadonlyArray<[string, string, boolean]> = [
  ['day_start', 'Старт дня', false], ['day_report', 'Итог дня', false], ['emergency', 'Аварийная остановка / KILL', true], ['service_down', 'Сбой сервиса и деградация платформы', true],
  ['live_gate', 'Запуск live', false], ['risk_level_changed', 'Смена уровня риска', false], ['brain_swap', 'Смена мозга / запасной профиль', false],
  ['position_closed', 'Закрытие позиции по стопу или тейку', false], ['day_close_failed', 'Закрытие дня не подтверждено', true], ['macro_blackout_soon', 'Скоро макро-событие', false],
]

/** Образцовые данные без сервера (?notify=mock | mock-quiet | mock-empty). Реальных команд не отправляется. */
export function mockNotify(kind: NotifyMock): NotifySnapshot {
  const empty = kind === 'mock-empty'
  const on = new Set(empty ? [] : ['day_start', 'day_report', 'emergency', 'service_down', 'day_close_failed', 'position_closed'])
  const settings: NotifySettings = {
    master: !empty,
    categories: Object.fromEntries(CATS.map(([id]) => [id, on.has(id)])),
    quiet: { enabled: !empty, from: '23:00', to: '07:00', tz: 'local', bypass: ['emergency', 'service_down', 'day_close_failed'] },
    limits: { per_hour: 6, same_kind_min: 10 },
  }
  const categories: NotifyCategory[] = CATS.map(([id, label, critical]) => ({ id, label, on: on.has(id), critical, bypass: settings.quiet.bypass.includes(id) }))
  return {
    source: empty ? 'config' : 'file', engine: !empty, warn: null, settings, categories, quiet_now: kind === 'mock-quiet', held_n: kind === 'mock-quiet' ? 3 : 0,
    sent_last: empty ? null : { cat: 'day_report' }, stats: empty ? { sent: 0, merged: 0, limited: 0, failed: 0 } : { sent: 14, merged: 3, limited: 1, failed: 0 },
    hint: 'Если уведомление не показывается: проверьте системные настройки уведомлений (macOS: «Редактор скриптов»; Windows: PowerShell и режим «Не беспокоить»).',
  }
}

/** Снимок после «принятия» настроек сервером (для отладки): категории и признаки пересчитаны. */
export function mockApplied(s: NotifySnapshot, to: NotifySettings): NotifySnapshot {
  return {
    ...s, source: 'file', engine: true, settings: to,
    categories: s.categories.map((c) => ({ ...c, on: to.categories[c.id] ?? false, bypass: to.quiet.bypass.includes(c.id) })),
  }
}
