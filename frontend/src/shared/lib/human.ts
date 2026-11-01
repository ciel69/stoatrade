import { humanCode, stepAmber, stepLabel } from './human-codes'
import { humanBanner, humanText } from './human-free'
import { humanGate, humanPre, humanWarning } from './human-gates'
import { humanNode } from './human-nodes'
import { detailsOf, make, unknownHuman } from './human-text'
import type { Human, HumanCtx, HumanItem } from './human-types'

/**
 * Главная функция человеческого слоя: любой служебный элемент (проверка H1–H8, предупреждение W*, шаг запуска S*, код ответа E_*, узел платформы, баннер, свободная фраза сервера)
 * превращается в Human: простая фраза «что происходит», «что это значит», «почему» и «что делать». Исходный текст сервера и код лежат только в details.
 * Неизвестное не показывается сырым: запасной текст по серьёзности.
 */
export function humanize(item: HumanItem, ctx: HumanCtx = {}): Human {
  switch (item.kind) {
    case 'gate': {
      const msg = item.msg ?? ''
      if (item.id === 'X') return make('info', { title: 'Проверки допуска к живой торговле сейчас не считаются', what: 'Платформа не смогла их вычислить.', why: 'Это не мешает бумажной торговле.', todo: 'Действий не требуется. Если не проходит долго, сообщите разработчику.' }, detailsOf(item.id, item.name, msg))
      const h = item.id.startsWith('W') ? humanWarning(item.id, msg, ctx) : item.id.startsWith('P') ? humanPre(item.id, item.ok, msg, item.sev) : humanGate(item.id, item.ok, msg, ctx)
      if (h) return h
      return item.ok ? make('ok', { title: 'Проверка пройдена', what: '', why: '', todo: '' }, detailsOf(item.id, item.name, msg)) : unknownHuman(item.sev === 'red' ? 'warn' : 'info', detailsOf(item.id, item.name, msg))
    }
    case 'warning': {
      const msg = item.msg ?? ''
      return humanWarning(item.id, msg, ctx) ?? humanPre(item.id, false, msg, 'amber') ?? unknownHuman('info', detailsOf(item.id, item.code, msg))
    }
    case 'step': {
      const label = stepLabel(item.name)
      const raw = detailsOf(item.name, item.code)
      if (item.ok === false) {
        const c = item.code ? humanCode(item.code) : null
        return make('bad', {
          title: `Запуск остановился на шаге «${label}»`,
          what: c ? `${c.title}. Торговля не включена.` : 'Торговля не включена.',
          why: c?.what ?? 'Шаг не прошёл проверку.',
          todo: c?.todo ?? 'Устраните причину и запустите день снова.',
        }, raw)
      }
      if (item.amber) {
        const a = stepAmber(item.name)
        return make(a?.tone ?? 'info', { title: a ? a.text[0].toUpperCase() + a.text.slice(1) : `Шаг «${label}» прошёл с замечаниями`, what: a?.what ?? '', why: '', todo: 'Действий не требуется.' }, raw)
      }
      return make('ok', { title: `${label}: готово`, what: '', why: '', todo: '' }, raw)
    }
    case 'node':
      return humanNode(item.node)
    case 'code':
      return humanCode(item.code, item.msg)
    case 'banner':
      return humanBanner(item.text, item.level)
    case 'text':
      return humanText(item.text, item.level)
  }
}
