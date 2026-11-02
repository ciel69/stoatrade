import { ageText, countdown, duration, gateName, humanize, isPlain, modeOn, nodeLabel, nodeStops, nodeValue, type Human, type Tone } from '@/shared/lib'
import { dayNeeds, dayWaitText } from '@/entities/day'
import { roleLabel } from '@/entities/role'
import type { SysNode } from '@/entities/system'
import { freshLevel, THRESHOLDS } from './thresholds'
import type { GroupId, StatusInput, StatusLevel, StatusRow } from './types'

/**
 * Строки окна состояния платформы. Каждая считается из данных, которые уже приходят по WebSocket (темы system, home, ctl, brain, market, news и состояние сокета).
 * Нет данных: нет строки. Тексты строк не пишутся здесь: служебные коды и фразы сервера переводит единый словарь (shared/lib/human), здесь только решение, в какую группу и с какой серьёзностью.
 * Главное правило серьёзности: проверки, которые относятся только к запуску живой торговли, пока платформа торгует иначе, это справка (idle), а не тревога.
 */

/** Возраст для коротких фраз: секунды до 10 с шагом, дальше минуты; чтобы текст не мерцал каждую секунду. */
export function coarse(ageS: number): string {
  const s = Math.max(0, ageS)
  return s < 60 ? `${Math.max(10, Math.floor(s / 10) * 10)} с` : duration(Math.round(s / 60) * 60)
}

const LEVEL_OF_TONE: Record<Tone, StatusLevel> = { ok: 'ok', info: 'idle', warn: 'warn', bad: 'bad' }
const EXEC_NODES = new Set(['exec', 'net', 'recon', 'events'])
const MARKET_NODES = new Set(['scan', 'scanner', 'feed', 'context'])
/** Проверки допуска, которые описывают работу платформы сейчас, а не готовность к живой торговле. */
const GATE_GROUP: Record<string, GroupId> = { H1: 'platform', H3: 'exec', H6: 'brain', W3: 'market', W6: 'platform', W8: 'news' }

export const lowerFirst = (s: string): string => (/^[А-ЯЁ][а-яё]/.test(s) ? s[0].toLowerCase() + s.slice(1) : s)

interface Opts { value?: string; brief?: string | null; stops?: boolean; extraNote?: string; name?: string }

/** Строка из объяснения. Проблема: заголовок становится названием строки, а «что это значит и что делать» уходит в пояснение. */
function fromHuman(group: GroupId, id: string, h: Human, o: Opts = {}): StatusRow {
  const level = LEVEL_OF_TONE[h.tone]
  const problem = h.tone === 'warn' || h.tone === 'bad'
  const info = h.tone === 'info'
  const note = problem || info ? [h.what, h.todo, o.extraNote].filter(Boolean).join(' ') || null : null
  return {
    id, group, level,
    label: problem || info ? h.title : (o.name ?? h.title),
    value: o.value ?? '',
    note,
    brief: problem ? (o.brief ?? h.title) : null,
    stops: problem && h.tone === 'bad' && !!o.stops,
    raw: h.details,
    human: h,
  }
}

function okRow(group: GroupId, id: string, name: string, value: string): StatusRow {
  return { id, group, label: name, level: 'ok', value, note: null, brief: null, stops: false, raw: null, human: null }
}

function idleRow(group: GroupId, id: string, name: string, value: string, note: string | null = null): StatusRow {
  return { id, group, label: name, level: 'idle', value, note, brief: null, stops: false, raw: null, human: null }
}

function say(title: string, what: string, why: string, todo: string, tone: Tone, details: string | null): Human {
  return { title, what, why, todo, tone, details }
}

function nodeRow(n: SysNode, now: number): StatusRow {
  const group: GroupId = EXEC_NODES.has(n.id) ? 'exec' : MARKET_NODES.has(n.id) ? 'market' : 'platform'
  const pre = /^pf-(P\d+)$/.exec(n.id)
  const h = pre
    ? humanize({ kind: 'gate', id: pre[1], ok: n.level === 'ok' || n.level === 'info', msg: n.text ?? '', sev: n.level === 'bad' ? 'red' : 'amber' })
    : humanize({ kind: 'node', node: n })
  const value = nodeValue(n, now)
  if (h.tone === 'ok') return okRow(group, `node:${n.id}`, nodeLabel(n.id, n.label), value)
  const age = n.at != null && (n.level === 'warn' || n.level === 'bad') ? `Последнее обновление: ${ageText(n.at, now)}.` : ''
  return fromHuman(group, `node:${n.id}`, h, { value, stops: n.level === 'bad' && nodeStops(n.id), extraNote: age, name: nodeLabel(n.id, n.label) })
}

const PLATFORM: Record<string, { text: string; tone: Tone; title?: string }> = {
  running: { text: 'работает', tone: 'ok' },
  starting: { text: 'запускается', tone: 'warn', title: 'Платформа запускается' },
  closing: { text: 'завершает день', tone: 'warn', title: 'Платформа завершает день' },
  halted: { text: 'остановлена аварийно', tone: 'bad', title: 'Платформа остановилась аварийно' },
  stopped: { text: 'остановлена', tone: 'info', title: 'Платформа остановлена' },
  unknown: { text: 'состояние неизвестно', tone: 'info', title: 'Состояние платформы пока неизвестно' },
}

const CTX_SOURCE: Record<string, string> = { fng: 'индекс страха и жадности', funding: 'ставки финансирования', macro: 'макро-календарь', oi: 'открытый интерес', liq: 'ликвидации' }

export function buildRows(inp: StatusInput): StatusRow[] {
  const { now, link, home, system, brain, market, news, ctl } = inp
  const rows: StatusRow[] = []

  // связь страницы с платформой
  if (link === 'open') rows.push(okRow('link', 'link', 'Связь с платформой', 'есть'))
  else if (link === 'connecting') rows.push(fromHuman('link', 'link', say('Подключаемся к платформе…', 'Данные появятся через несколько секунд.', '', 'Действий не требуется.', 'warn', 'link: connecting'), { brief: 'Подключаемся к платформе…' }))
  else rows.push(fromHuman('link', 'link', say('Нет связи с платформой', 'Страница не получает данные, показанное может быть устаревшим. Платформа могла продолжить торговать сама: защитные стопы стоят на бирже.', 'Соединение потеряно.', 'Страница переподключится сама. Если не получается, проверьте, что платформа запущена.', 'bad', 'link: closed'), { brief: 'Нет связи с платформой' }))

  // платформа: состояние и режим
  const pState = ctl?.platform.state ?? home?.platform.state ?? null
  const mode = ctl?.platform.mode ?? system?.mode ?? home?.platform.mode ?? null
  const trading = ctl?.platform.trading ?? home?.platform.trading ?? null
  const stopped = pState === 'stopped'
  const running = pState === 'running'
  const ctx = { mode, running }
  if (pState) {
    const p = PLATFORM[pState] ?? PLATFORM.unknown
    const how = ctl?.platform.observe ? 'только наблюдение' : trading === true ? `торгует ${modeOn(mode)}` : trading === false ? 'торговля выключена' : ''
    const value = pState === 'running' ? [p.text, how].filter(Boolean).join(' · ') : p.text
    if (p.tone === 'ok') rows.push(okRow('platform', 'platform', 'Платформа', value))
    else {
      const note = stopped ? 'Торговля не идёт. Запустить день можно в разделе «Управление».' : pState === 'halted' ? 'Торговля остановлена. Причина указана в сообщении платформы или в подробностях.' : 'Подождите: это временное состояние.'
      const h = say(p.title ?? 'Платформа', note, '', stopped ? 'Чтобы торговать, запустите день.' : pState === 'halted' ? 'Разберитесь с причиной и запустите день заново.' : 'Действий не требуется.', p.tone, `platform.state=${pState}`)
      const r = fromHuman('platform', 'platform', h, { value: p.text, brief: stopped ? 'Платформа остановлена' : p.title, stops: pState === 'halted' })
      r.note = note + (h.todo ? ` ${h.todo}` : '')
      rows.push(r)
    }
  }
  if (system || ctl) {
    const k = system?.kill ?? ctl?.kill ?? null
    if (k) {
      const h = humanize({ kind: 'gate', id: 'H1', ok: false, msg: `KILL активен (причина: ${k.reason ?? '-'})` }, ctx)
      rows.push(fromHuman('platform', 'kill', h, { stops: true, brief: 'Включена аварийная остановка' }))
    } else rows.push(okRow('platform', 'kill', 'Аварийная остановка', 'не включена'))
  }
  if (ctl) {
    rows.push(ctl.alive ? okRow('platform', 'ctl', 'Управление днём', 'отвечает')
      : fromHuman('platform', 'ctl', say('Служба управления не отвечает', 'Кнопки запуска и остановки дня сейчас не работают. Торговля, если она шла, продолжается.', 'Служба управления давно не обновляла статус.', 'Подождите минуту. Если не вернулась, перезапустите платформу.', 'warn', 'ctl.alive=false'), { brief: 'Не отвечает служба управления' }))
    if (ctl.banner?.text) {
      const h = humanize({ kind: 'banner', level: ctl.banner.level, text: ctl.banner.text }, ctx)
      rows.push(fromHuman('platform', 'banner', h, { value: '', brief: h.title }))
    }
  }
  // торговый день ждёт вашего решения: только факт и ссылка на страницу «День»
  const wait = dayWaitText(dayNeeds(inp.day))
  if (wait) rows.push({ id: 'day-wait', group: 'platform', label: 'Ждёт вашего ответа', level: 'warn', value: '', note: `Нужно ваше участие: ${wait}.`, brief: 'Ждёт вашего ответа', stops: false, raw: null, human: null, href: '/trading/day' })
  const allowed = system?.room.allowed ?? home?.allowed ?? null
  if (allowed !== null) {
    if (allowed) rows.push(okRow('platform', 'entries', 'Новые сделки', 'разрешены'))
    else {
      const reasons = system?.room.reasons.length ? system.room.reasons : system?.session?.block_reasons ?? []
      const hs = reasons.map((x) => humanize({ kind: 'text', level: 'warn', text: x }, ctx))
      const worst = hs.find((x) => x.tone === 'bad') ?? hs.find((x) => x.tone === 'warn') ?? hs[0]
      if (!worst) rows.push(fromHuman('platform', 'entries', say('Новые сделки сейчас закрыты', 'Платформа не открывает новые позиции, открытые продолжает сопровождать.', 'Лимиты или правила сессии не позволяют входить.', 'Действий не требуется.', 'warn', null), {}))
      else {
        const extra = hs.length > 1 ? `Ещё причин: ${hs.length - 1}.` : ''
        const r = fromHuman('platform', 'entries', worst, { stops: worst.tone === 'bad', brief: worst.title, extraNote: extra })
        r.raw = reasons.join('; ') || null
        rows.push(r)
      }
    }
  }
  for (const [i, w] of (system?.warnings ?? []).entries()) {
    if (w.text) rows.push(fromHuman('platform', `warning:${i}`, humanize({ kind: 'text', level: w.level ?? 'info', text: w.text }, ctx), { stops: false }))
  }

  // узлы платформы
  if (system) {
    for (const n of system.nodes) rows.push(nodeRow(n, now))
  } else if (home) {
    for (const i of home.platform.items) {
      const n: SysNode = { id: i.id === 'scanner' ? 'scan' : i.id, label: i.label, level: i.level ?? 'off', text: i.value }
      rows.push(nodeRow(n, now))
    }
  }

  // мозг
  if (brain) {
    const st = brain.status
    if (st === 'down') rows.push(fromHuman('brain', 'brain', say('Мозг не отвечает', 'Мозг — это ИИ-агент, который пишет торговые планы. Пока он молчит, новых планов не будет; открытые позиции защищает исполнитель.', 'Нет пульса мозга больше 30 секунд.', 'Подождите минуту. Если не вернулся, проверьте ключ и лимиты расходов в «Настройках».', 'bad', 'brain.status=down'), { brief: 'Не отвечает мозг' }))
    else if (st === 'protect') rows.push(fromHuman('brain', 'brain', say('Мозг работает только на защиту позиций', 'Новые сделки он не открывает, только сопровождает открытые.', 'Сработало ограничение, из-за которого рисковать нельзя.', 'Действий не требуется: режим снимется сам.', 'warn', `brain.status=protect | ${brain.protect_reason ?? ''}`), { brief: 'Мозг только защищает позиции' }))
    else rows.push(okRow('brain', 'brain', 'Мозг', st === 'thinking' ? 'думает' : 'ждёт событий'))
    if (brain.health === 'yellow' || brain.health === 'red') {
      rows.push(fromHuman('brain', 'brain-health', say('Мозг сообщает о проблемах', 'Торговля продолжается, но качество планов может снизиться.', 'Мозг оценивает своё состояние как неблагополучное.', 'Откройте «Настройки», раздел моделей, и проверьте ответы.', brain.health === 'red' ? 'bad' : 'warn', `brain.health=${brain.health}`), { brief: 'Мозг сообщает о проблемах' }))
    }
    const failed = brain.roles.filter((r) => r.last && r.last.ok === false).map((r) => roleLabel(r.role))
    const spent = brain.roles.filter((r) => r.today && r.today.budget_left_usd != null && r.today.budget_left_usd <= 0).map((r) => roleLabel(r.role))
    const cooling = (brain.cooling ?? []).map((r) => roleLabel(r))
    if (brain.roles.length) {
      const anyCall = brain.roles.some((r) => r.last)
      const raw = `failed=${brain.roles.filter((r) => r.last && r.last.ok === false).map((r) => r.role).join(',')} cooling=${(brain.cooling ?? []).join(',')} spent=${brain.roles.filter((r) => r.today && r.today.budget_left_usd != null && r.today.budget_left_usd <= 0).map((r) => r.role).join(',')}`
      if (spent.length) rows.push(fromHuman('brain', 'llm', say('Исчерпан бюджет расходов на модели', `Затронуто: ${spent.join(', ')}. Эти роли мозга не смогут отвечать до конца окна.`, 'Дневной или часовой предел расходов достигнут.', 'Лимиты меняет только владелец в «Настройках».', 'warn', raw), { brief: 'Исчерпан бюджет моделей' }))
      else if (failed.length) rows.push(fromHuman('brain', 'llm', say('Модель не ответила на последний запрос', `Затронуто: ${failed.join(', ')}. Мозг может использовать запасную модель.`, 'Провайдер модели временно недоступен или отказал.', 'Обычно проходит само. Если повторяется, проверьте ключ в «Настройках».', 'warn', raw), { brief: 'Модель не ответила' }))
      else if (cooling.length) rows.push(fromHuman('brain', 'llm', say('Часть моделей на паузе', `Затронуто: ${cooling.join(', ')}. Платформа даёт им отдохнуть после ошибок.`, 'Модели недавно отвечали с ошибками.', 'Действий не требуется: пауза закончится сама.', 'warn', raw), { brief: 'Часть моделей на паузе' }))
      else rows.push(anyCall ? okRow('brain', 'llm', 'Модели мозга', 'отвечают без ошибок') : idleRow('brain', 'llm', 'Модели мозга', 'недавних запросов не было'))
    }
  }

  // данные по монетам и рынку
  if (market) {
    if (market.heat.at != null) {
      const age = now - market.heat.at
      const lv = freshLevel(age, THRESHOLDS.heatS)
      rows.push(lv === 'ok' || lv === 'idle' ? okRow('market', 'heat', 'Карта рынка', `обновлена ${ageText(market.heat.at, now)}`)
        : fromHuman('market', 'heat', say(lv === 'bad' ? 'Карта рынка не обновляется' : 'Карта рынка обновляется с опозданием', `Последнее обновление было ${coarse(age)} назад. Цены на карте могут быть устаревшими.`, 'Служба сканера отстаёт или остановилась.', 'Обычно проходит само. Если держится, перезапустите платформу.', lv === 'bad' ? 'warn' : 'warn', `heat.age=${Math.round(age)}`), { brief: 'Карта рынка отстаёт' }))
    } else if (!market.heat.available) {
      rows.push(idleRow('market', 'heat', 'Карта рынка', 'данных нет'))
    }
    if (!system && market.coins.at != null) {
      const age = now - market.coins.at
      const lv = freshLevel(age, THRESHOLDS.coinsS)
      rows.push(lv === 'ok' || lv === 'idle' ? okRow('market', 'coins', 'Цены монет', `обновлены ${ageText(market.coins.at, now)}`)
        : fromHuman('market', 'coins', say('Цены монет приходят с задержкой', `Последние цены были ${coarse(age)} назад.`, 'Служба цен отстаёт.', 'Обычно проходит само. Если держится, перезапустите платформу.', lv === 'bad' ? 'bad' : 'warn', `coins.age=${Math.round(age)}`), { brief: 'Цены монет отстают', stops: lv === 'bad' }))
    }
  }
  if (system) {
    const feed = system.sources.feed
    if (feed.length) {
      const bad = feed.filter((s) => (s.state != null && s.state !== 'ok') || s.connected === false)
      const raw = bad.map((s) => `${s.id}: ${s.error ?? s.state ?? 'нет соединения'}`).join('; ')
      rows.push(bad.length
        ? fromHuman('market', 'feed-sources', say(bad.length === feed.length ? 'Источники цен недоступны' : 'Часть источников цен недоступна', `Работают ${feed.length - bad.length} из ${feed.length}. ${bad.length === feed.length ? 'Без цен нельзя безопасно открывать сделки.' : 'Платформа берёт цены из оставшихся, поэтому торговля продолжается.'}`, 'Источник временно не отвечает.', 'Обычно проходит само.', bad.length === feed.length ? 'bad' : 'warn', raw), { brief: bad.length === feed.length ? 'Источники цен недоступны' : 'Часть источников цен недоступна', stops: bad.length === feed.length })
        : okRow('market', 'feed-sources', 'Источники цен', `работают ${feed.length} из ${feed.length}`))
    }
    const deg = system.sources.degraded
    const ctxKeys = Object.keys(system.sources.context)
    if (deg.length) {
      const names = deg.map((d) => CTX_SOURCE[d]).filter(Boolean)
      rows.push(fromHuman('market', 'ctx-sources', say('Часть источников справочных данных не отвечает', `${names.length ? `Не отвечают: ${names.join(', ')}. ` : ''}Мозг получает меньше сведений о рынке. Торговля идёт.`, 'Внешние сервисы временно недоступны.', 'Обычно проходит само.', 'warn', `degraded: ${deg.join(', ')}`), { brief: 'Часть справочных данных недоступна' }))
    } else if (ctxKeys.length) rows.push(okRow('market', 'ctx-sources', 'Справочные данные рынка', 'поступают'))
  }

  // новости
  if (news) {
    const srcs = news.sources
    const failing = new Set<string>([...srcs.filter((s) => !s.ok || s.degraded).map((s) => s.id), ...news.degraded])
    if (srcs.length || failing.size) {
      const total = Math.max(srcs.length, failing.size)
      const list = [...failing].filter((s) => isPlain(s) || /^[a-z0-9.\-]+$/i.test(s)).join(', ')
      rows.push(failing.size
        ? fromHuman('news', 'news-sources', say(failing.size >= total ? 'Ленты новостей не отвечают' : 'Не все ленты новостей отвечают', `Работают ${total - failing.size} из ${total}${list ? `; не отвечают: ${list}` : ''}. Торговлю это не останавливает, но мозг видит меньше новостей.`, 'Сайты новостей временно недоступны.', 'Обычно проходит само.', failing.size >= total ? 'warn' : 'warn', `news: ${[...failing].join(', ')}`), { brief: failing.size >= total ? 'Ленты новостей не отвечают' : 'Часть лент новостей не отвечает' })
        : okRow('news', 'news-sources', 'Ленты новостей', `работают ${total} из ${total}`))
    }
    const stamps = news.items.map((i) => i.ts).filter((t): t is number => typeof t === 'number')
    if (stamps.length) {
      const last = Math.max(...stamps)
      const age = now - last
      rows.push(age > THRESHOLDS.newsQuietS
        ? fromHuman('news', 'news-fresh', say('Новости давно не обновлялись', `Последний заголовок был ${coarse(age)} назад.`, 'Ленты молчат или перестали отвечать.', 'Обычно проходит само. Если держится, посмотрите раздел «Новости».', 'warn', `news.age=${Math.round(age)}`), { brief: 'Новости давно не обновлялись' })
        : okRow('news', 'news-fresh', 'Свежесть новостей', `последний заголовок ${ageText(last, now)}`))
    } else rows.push(idleRow('news', 'news-fresh', 'Свежесть новостей', 'заголовков нет'))
    const rd = news.reader
    rows.push(!rd.enabled ? idleRow('news', 'reader', 'Разбор новостей', 'выключен')
      : rd.stale ? fromHuman('news', 'reader', say('Разбор новостей отстаёт', 'Заголовки приходят, но не размечаются по важности.', 'Служба разметки давно не работала.', 'Действий не требуется: торговля идёт.', 'warn', 'news.reader.stale'), { brief: 'Разбор новостей отстаёт' })
        : okRow('news', 'reader', 'Разбор новостей', 'работает'))
    const bo = news.calendar.blackout
    const next = news.calendar.next[0]
    if (bo) rows.push(idleRow('news', 'calendar', 'Календарь событий', 'идёт окно важной новости', 'Новые входы закрыты на время события.'))
    else if (next) rows.push(okRow('news', 'calendar', 'Календарь событий', `${next.type} через ${countdown(next.at - now)}`))
  }

  // проверки допуска к живой торговле и предупреждения запуска (платформа считает их всегда)
  const killed = !!(system?.kill ?? ctl?.kill)
  const liveMode = mode === 'live'
  for (const c of ctl?.checks ?? []) {
    if (c.id === 'H1' && (killed || c.ok)) continue          // аварийная остановка уже есть отдельной строкой
    const h = humanize({ kind: 'gate', id: c.id, name: c.name, ok: c.ok, msg: c.msg, sev: c.sev }, ctx)
    const general = GATE_GROUP[c.id]
    const group: GroupId = general ?? (h.tone === 'bad' || h.tone === 'warn' ? 'platform' : 'live')
    const name = gateName(c.id) ?? c.name
    if (c.ok) { rows.push(okRow(general ?? 'live', `gate:${c.id}`, name, /проверится после|будет переключ/.test(c.msg) ? 'проверится при запуске живой торговли' : 'в порядке')); continue }
    const r = fromHuman(group, `gate:${c.id}`, h, { name, stops: h.tone === 'bad' && (c.id === 'H1' || (c.id === 'H3' && /не отвечает/.test(c.msg)) || (liveMode && ['H2', 'H4', 'H5'].includes(c.id))), brief: h.title })
    rows.push(r)
  }
  // одна сводная строка: почему живая торговля пока недоступна
  if (!liveMode) {
    const blockers = rows.filter((r) => r.group === 'live' && r.level === 'idle' && r.id.startsWith('gate:H'))
    if (blockers.length) {
      const first = blockers.find((r) => r.id === 'gate:H7') ?? blockers[0]
      const more = blockers.length > 1 ? ` Ещё условий для живой торговли: ${blockers.length - 1}; они в списке «Все проверки».` : ''
      const what = (first.human?.what ?? '').replace(/ Пока платформа торгует на бумаге, это ни на что не влияет\./, '')
      rows.push({ id: 'live-readiness', group: 'live', label: first.human?.title ?? 'Живая торговля пока не включена', level: 'idle', value: '', note: `${what} ${first.human?.todo ?? ''}${more}`.trim(), brief: null, stops: false, raw: null, human: first.human })
    }
  }

  return demote(rows, link, stopped)
}

/** Платформа остановлена: исполнитель, биржа, мозг и служба управления не должны работать, их «не запущен» не проблема. Нет связи: остальные данные могут быть неактуальны. */
function demote(rows: StatusRow[], link: StatusInput['link'], stopped: boolean): StatusRow[] {
  return rows.map((r) => {
    if (r.id === 'link' || (r.level !== 'warn' && r.level !== 'bad' && r.level !== 'ok')) return r
    if (link !== 'open') return { ...r, level: 'idle', note: 'Нет связи с платформой: данные могут быть устаревшими.', brief: null, stops: false }
    if (stopped && (r.group === 'exec' || r.group === 'brain' || r.id === 'ctl' || r.id === 'gate:H3' || r.id === 'gate:H6') && r.level !== 'ok') return { ...r, level: 'idle', brief: null, stops: false, note: `${r.note ?? ''} Платформа остановлена, поэтому это ожидаемо.`.trim() }
    return r
  })
}
