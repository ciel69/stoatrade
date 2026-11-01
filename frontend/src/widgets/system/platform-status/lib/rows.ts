import { ageText, countdown, duration } from '@/shared/lib'
import { nodeLine, type SysLevel, type SysNode } from '@/entities/system'
import { freshLevel, THRESHOLDS } from './thresholds'
import type { GroupId, StatusInput, StatusLevel, StatusRow } from './types'

/**
 * Строки окна состояния платформы. Каждая строка считается из данных, которые уже приходят по WebSocket (темы system, home, ctl, brain, market, news и состояние сокета).
 * Нет данных: нет строки (статус не выдумывается). Уровень узлов платформы берётся у сервера как есть; свежесть карты рынка, заголовков новостей и мозга считается здесь.
 */

/** Возраст для коротких фраз: секунды до 10 с шагом, дальше минуты; чтобы текст в шапке не мерцал каждую секунду. */
export function coarse(ageS: number): string {
  const s = Math.max(0, ageS)
  return s < 60 ? `${Math.max(10, Math.floor(s / 10) * 10)} с` : duration(Math.round(s / 60) * 60)
}

const SYS: Record<SysLevel, StatusLevel> = { ok: 'ok', info: 'ok', warn: 'warn', bad: 'bad', off: 'idle' }
const EXEC_NODES = new Set(['exec', 'net', 'recon', 'events'])
const MARKET_NODES = new Set(['scan', 'feed', 'context'])

interface Opts { reason?: string | null; brief?: string | null }

function row(group: GroupId, id: string, label: string, level: StatusLevel, value: string, o: Opts = {}): StatusRow {
  const bad = level === 'warn' || level === 'bad'
  return { id, group, label, level, value, reason: level === 'ok' ? null : (o.reason ?? null), brief: bad ? (o.brief ?? o.reason ?? `${label}: ${value}`) : level === 'idle' ? (o.brief ?? null) : null }
}

function nodeRow(n: SysNode, now: number): StatusRow {
  const level = SYS[n.level] ?? 'idle'
  const group: GroupId = EXEC_NODES.has(n.id) ? 'exec' : MARKET_NODES.has(n.id) ? 'market' : 'platform'
  const line = nodeLine(n, now)
  const brief = n.at != null ? `${n.label}: не обновляется ${coarse(now - n.at)}` : n.text ? `${n.label}: ${n.text}` : `${n.label}: ${level === 'bad' ? 'проблема' : 'требует внимания'}`
  if (level !== 'warn' && level !== 'bad') return row(group, `node:${n.id}`, n.label, level, line)
  // при проблеме значение это возраст (или слово уровня), а причина это текст сервера: так одно и то же не повторяется в двух местах
  const age = n.at != null ? `обновлено ${ageText(n.at, now)}` : n.since != null ? `работает ${duration(now - n.since)}` : null
  return row(group, `node:${n.id}`, n.label, level, age ?? (level === 'bad' ? 'проблема' : 'внимание'), { reason: n.text ?? (n.at != null ? 'сервер отметил: данные отстают' : null), brief })
}

const BANNER: Record<string, StatusLevel> = { bad: 'bad', red: 'bad', error: 'bad', crit: 'bad', warn: 'warn', amber: 'warn', yellow: 'warn' }
const PLATFORM_TEXT: Record<string, { text: string; level: StatusLevel }> = {
  running: { text: 'работает', level: 'ok' },
  starting: { text: 'запускается', level: 'warn' },
  closing: { text: 'останавливается', level: 'warn' },
  halted: { text: 'остановлена аварийно', level: 'bad' },
  stopped: { text: 'остановлена', level: 'idle' },
  unknown: { text: 'состояние неизвестно', level: 'idle' },
}

export function buildRows(inp: StatusInput): StatusRow[] {
  const { now, link, home, system, brain, market, news, ctl } = inp
  const rows: StatusRow[] = []

  // связь дашборда с сервером
  rows.push(link === 'open' ? row('link', 'link', 'Связь дашборда с сервером', 'ok', 'подключён')
    : link === 'connecting' ? row('link', 'link', 'Связь дашборда с сервером', 'warn', 'подключение…', { reason: 'дашборд устанавливает соединение с сервером', brief: 'подключение к платформе…' })
      : row('link', 'link', 'Связь дашборда с сервером', 'bad', 'нет связи', { reason: 'соединение с сервером потеряно, дашборд переподключается сам', brief: 'нет связи с платформой' }))

  // платформа: состояние и режим
  const pState = ctl?.platform.state ?? home?.platform.state ?? null
  const pMode = ctl?.platform.mode ?? home?.platform.mode ?? null
  const trading = ctl?.platform.trading ?? home?.platform.trading ?? null
  const stopped = pState === 'stopped'
  if (pState) {
    const p = PLATFORM_TEXT[pState] ?? { text: pState, level: 'idle' as StatusLevel }
    const extra = [pMode, ctl?.platform.observe ? 'наблюдение' : trading === true ? 'торговля' : trading === false ? 'без торговли' : null].filter(Boolean).join(' · ')
    rows.push(row('platform', 'platform', 'Платформа', p.level, extra && pState !== 'stopped' ? `${p.text} · ${extra}` : p.text, { reason: p.level === 'warn' || p.level === 'bad' ? `платформа ${p.text}` : null, brief: stopped ? 'платформа остановлена' : `платформа ${p.text}` }))
  }
  if (system || ctl) {
    const k = system?.kill ?? ctl?.kill ?? null
    rows.push(k ? row('platform', 'kill', 'Аварийная остановка (KILL)', 'bad', 'активна', { reason: `${k.reason ? `Причина: ${k.reason}. ` : ''}Снять: ./tb exec resume в терминале`, brief: 'активна аварийная остановка' })
      : row('platform', 'kill', 'Аварийная остановка (KILL)', 'ok', 'не активна'))
  }
  const health = ctl?.platform.health
  if (health === 'green' || health === 'yellow' || health === 'red') {
    const lv: StatusLevel = health === 'green' ? 'ok' : health === 'yellow' ? 'warn' : 'bad'
    rows.push(row('platform', 'health', 'Здоровье платформы', lv, health === 'green' ? 'в норме' : health === 'yellow' ? 'есть замечания' : 'критично', { reason: `служба управления оценивает состояние как ${health === 'yellow' ? 'жёлтое' : 'красное'}`, brief: `платформа: ${health === 'yellow' ? 'есть замечания' : 'критичное состояние'}` }))
  }
  if (ctl) {
    rows.push(ctl.alive ? row('platform', 'ctl', 'Служба управления', 'ok', 'отвечает') : row('platform', 'ctl', 'Служба управления', 'warn', 'не отвечает', { reason: 'служба управления давно не обновляла статус', brief: 'служба управления не отвечает' }))
    if (ctl.banner?.text) rows.push(row('platform', 'banner', 'Сообщение платформы', BANNER[String(ctl.banner.level).toLowerCase()] ?? 'idle', ctl.banner.text, { reason: ctl.banner.text, brief: ctl.banner.text }))
  }
  const allowed = system?.room.allowed ?? home?.allowed ?? null
  if (allowed !== null) {
    const why = (system?.room.reasons.length ? system.room.reasons : system?.session?.block_reasons ?? []).join('; ')
    rows.push(allowed ? row('platform', 'entries', 'Новые входы', 'ok', 'разрешены') : row('platform', 'entries', 'Новые входы', 'warn', 'закрыты', { reason: why || 'лимиты или правила сессии не позволяют входить', brief: `входы закрыты${why ? `: ${why}` : ''}` }))
  }
  for (const [i, w] of (system?.warnings ?? []).entries()) {
    if (w.text) rows.push(row('platform', `warning:${i}`, 'Предупреждение', SYS[w.level ?? 'info'] ?? 'idle', w.text, { reason: w.text, brief: w.text }))
  }

  // узлы платформы: исполнитель, биржа, сверка, шина, сканер, фид, контекст, сессия (уровень от сервера)
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
    rows.push(st === 'down' ? row('brain', 'brain', 'Мозг (агент)', 'bad', 'не отвечает', { reason: 'нет пульса мозга: он не обновлял статус дольше 30 с', brief: 'мозг не отвечает' })
      : st === 'protect' ? row('brain', 'brain', 'Мозг (агент)', 'warn', 'только защита', { reason: brain.protect_reason ?? 'мозг ограничен защитой открытых позиций', brief: 'мозг: только защита' })
        : row('brain', 'brain', 'Мозг (агент)', 'ok', st === 'thinking' ? 'думает' : 'ждёт событий'))
    if (brain.health === 'yellow' || brain.health === 'red') {
      rows.push(row('brain', 'brain-health', 'Здоровье мозга', brain.health === 'yellow' ? 'warn' : 'bad', brain.health === 'yellow' ? 'есть замечания' : 'критично', { reason: 'мозг сообщает о своём состоянии как ' + (brain.health === 'yellow' ? 'жёлтом' : 'красном'), brief: 'здоровье мозга: ' + (brain.health === 'yellow' ? 'замечания' : 'критично') }))
    }
    const failed = brain.roles.filter((r) => r.last && r.last.ok === false).map((r) => r.role)
    const spent = brain.roles.filter((r) => r.today && r.today.budget_left_usd != null && r.today.budget_left_usd <= 0).map((r) => r.role)
    const cooling = brain.cooling ?? []
    const issues = [failed.length ? `последний вызов не удался: ${failed.join(', ')}` : '', cooling.length ? `на паузе: ${cooling.join(', ')}` : '', spent.length ? `бюджет исчерпан: ${spent.join(', ')}` : ''].filter(Boolean)
    if (brain.roles.length) {
      const anyCall = brain.roles.some((r) => r.last)
      rows.push(issues.length ? row('brain', 'llm', 'Модели (LLM)', 'warn', issues[0] ?? '', { reason: issues.join('; '), brief: `модели: ${issues[0]}` })
        : row('brain', 'llm', 'Модели (LLM)', anyCall ? 'ok' : 'idle', anyCall ? 'ответы без ошибок' : 'недавних вызовов не было'))
    }
  }

  // данные по монетам и рынку
  if (market) {
    if (market.heat.at != null) {
      const age = now - market.heat.at
      const lv = freshLevel(age, THRESHOLDS.heatS)
      rows.push(row('market', 'heat', 'Карта рынка (сканер)', lv, `обновлена ${ageText(market.heat.at, now)}`, { reason: `карта не обновляется ${coarse(age)}, порог ${duration(THRESHOLDS.heatS)}`, brief: `карта рынка не обновляется ${coarse(age)}` }))
    } else if (!market.heat.available) {
      rows.push(row('market', 'heat', 'Карта рынка (сканер)', 'idle', 'данных нет'))
    }
    if (!system && market.coins.at != null) {
      const age = now - market.coins.at
      rows.push(row('market', 'coins', 'Цены монет (фид)', freshLevel(age, THRESHOLDS.coinsS), `обновлены ${ageText(market.coins.at, now)}`, { reason: `цены не обновляются ${coarse(age)}, порог ${duration(THRESHOLDS.coinsS)}`, brief: `цены монет не обновляются ${coarse(age)}` }))
    }
  }
  if (system) {
    const feed = system.sources.feed
    if (feed.length) {
      const bad = feed.filter((s) => (s.state != null && s.state !== 'ok') || s.connected === false)
      rows.push(bad.length ? row('market', 'feed-sources', 'Источники цен', bad.length === feed.length ? 'bad' : 'warn', `${feed.length - bad.length} из ${feed.length} подключены`, { reason: bad.map((s) => `${s.id}: ${s.error ?? s.state ?? 'нет соединения'}`).join('; '), brief: `источники цен: ${bad.map((s) => s.id).join(', ')} недоступны` })
        : row('market', 'feed-sources', 'Источники цен', 'ok', `${feed.length} из ${feed.length} подключены`))
    }
    const deg = system.sources.degraded
    const ctxKeys = Object.keys(system.sources.context)
    if (deg.length) rows.push(row('market', 'ctx-sources', 'Источники контекста рынка', 'warn', `недоступны: ${deg.join(', ')}`, { reason: `не отвечают: ${deg.join(', ')}`, brief: `контекст рынка: ${deg.join(', ')} не отвечают` }))
    else if (ctxKeys.length) rows.push(row('market', 'ctx-sources', 'Источники контекста рынка', 'ok', `${ctxKeys.length} отвечают`))
  }

  // новости
  if (news) {
    const srcs = news.sources
    const failing = new Set<string>([...srcs.filter((s) => !s.ok || s.degraded).map((s) => s.id), ...news.degraded])
    if (srcs.length || failing.size) {
      const total = Math.max(srcs.length, failing.size)
      const list = [...failing].join(', ')
      rows.push(failing.size ? row('news', 'news-sources', 'Ленты новостей', failing.size >= total ? 'bad' : 'warn', `${total - failing.size} из ${total} работают`, { reason: `не отвечают: ${list}`, brief: failing.size >= total ? 'ленты новостей не отвечают' : failing.size === 1 ? `новости: лента ${list} не отвечает` : `новости: ${failing.size} ленты не отвечают` })
        : row('news', 'news-sources', 'Ленты новостей', 'ok', `${total} из ${total} работают`))
    }
    const stamps = news.items.map((i) => i.ts).filter((t): t is number => typeof t === 'number')
    if (stamps.length) {
      const last = Math.max(...stamps)
      const age = now - last
      rows.push(row('news', 'news-fresh', 'Свежесть новостей', age > THRESHOLDS.newsQuietS ? 'warn' : 'ok', `последний заголовок ${ageText(last, now)}`, { reason: `новых заголовков нет ${coarse(age)}, порог ${duration(THRESHOLDS.newsQuietS)}`, brief: `новости не обновляются ${coarse(age)}` }))
    } else {
      rows.push(row('news', 'news-fresh', 'Свежесть новостей', 'idle', 'заголовков нет'))
    }
    const rd = news.reader
    rows.push(!rd.enabled ? row('news', 'reader', 'Читатель новостей', 'idle', 'выключен')
      : rd.stale ? row('news', 'reader', 'Читатель новостей', 'warn', 'разметка устарела', { reason: 'заголовки приходят, но читатель давно их не размечал', brief: 'разметка новостей устарела' })
        : row('news', 'reader', 'Читатель новостей', 'ok', rd.status && rd.status !== 'ok' ? rd.status : 'работает'))
    const bo = news.calendar.blackout
    const next = news.calendar.next[0]
    if (bo) rows.push(row('news', 'calendar', 'Календарь событий', 'idle', `окно блокировки: ${bo.name || 'событие'}`, { reason: 'новые входы закрыты на время события' }))
    else if (next) rows.push(row('news', 'calendar', 'Календарь событий', 'ok', `${next.type} через ${countdown(next.at - now)}`))
  }

  // гейты H1-H8 и предупреждения старта
  for (const c of ctl?.checks ?? []) {
    const red = c.sev === 'red'
    const lv: StatusLevel = c.ok ? 'ok' : red ? 'bad' : 'warn'
    rows.push(row('gates', `gate:${c.id}`, red ? `${c.id} · ${c.name}` : `Предупреждение ${c.id}`, lv, c.ok ? (c.msg || 'пройден') : red ? 'не пройден' : 'предупреждение', { reason: c.msg || c.name, brief: `${red ? c.name : 'старт'}: ${c.msg || 'не пройден'}` }))
  }

  return demote(rows, link, stopped)
}

/** Платформа остановлена: исполнитель, биржа, мозг и служба управления не должны работать, их «не запущен» не проблема. Нет связи: остальные данные могут быть неактуальны. */
function demote(rows: StatusRow[], link: StatusInput['link'], stopped: boolean): StatusRow[] {
  return rows.map((r) => {
    if (r.id === 'link' || (r.level !== 'warn' && r.level !== 'bad' && r.level !== 'ok')) return r
    if (link !== 'open') return { ...r, level: 'idle', reason: 'нет связи: данные могут быть неактуальны', brief: null }
    if (stopped && (r.group === 'exec' || r.group === 'brain' || r.id === 'ctl') && r.level !== 'ok') return { ...r, level: 'idle', reason: `${r.reason ?? r.value} (платформа остановлена)`, brief: null }
    return r
  })
}
