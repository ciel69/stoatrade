/**
 * Клиент WebSocket дашборда. Опроса нет: сервер сам присылает снимок тем при подключении и дальше только изменения.
 * Обрыв: переподключение с нарастающей паузой (1, 2, 4 ... 15 с, с разбросом). Тишина дольше SILENCE_MS (сервер шлёт heartbeat `link` раз в 15 с) считается обрывом.
 */
export type LinkState = 'connecting' | 'open' | 'closed'

export interface LiveMessage {
  topic: string
  seq: number
  ts: number
  data: unknown
}

/** Разовые сообщения (ответ на команду, пульс связи): повторять новому подписчику их нельзя. */
const EPHEMERAL = new Set(['pong', 'link', 'plan_detail', 'usage_detail', 'day_report_detail', 'exp_detail', 'learning_detail', 'ctl_result', 'ctl_final',
  'memory_cases_detail', 'memory_lessons_detail', 'memory_rules_detail', 'memory_path_detail', 'memory_action_result', 'cmkt_sym_detail', 'exits_status_detail'])

type MessageHandler = (m: LiveMessage) => void
type StateHandler = (s: LinkState) => void

export interface LiveSocketOptions {
  url: string
  /** Фабрика сокета (в тестах подменяется) */
  factory?: (url: string) => WebSocket
  backoffBaseMs?: number
  backoffMaxMs?: number
  silenceMs?: number
  random?: () => number
  /**
   * Разовый вопрос серверу после неудачного подключения: сколько сокетов занято (GET /api/link). Браузерный WebSocket не отдаёт код HTTP, поэтому отказ 503
   * («слишком много вкладок») от обрыва сети иначе не отличить. null: ответа нет. Не опрос: не больше PROBE_MAX запросов за один обрыв.
   */
  probe?: () => Promise<{ clients: number; max: number } | null>
}

const PROBE_MAX = 3
type RefusalHandler = (full: boolean) => void

export class LiveSocket {
  private ws: WebSocket | null = null
  private handlers = new Map<string, Set<MessageHandler>>()
  private last = new Map<string, LiveMessage>()
  private stateHandlers = new Set<StateHandler>()
  private attempt = 0
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private watchdog: ReturnType<typeof setInterval> | null = null
  private lastRx = 0
  private stopped = true
  private refusalHandlers = new Set<RefusalHandler>()
  private probes = 0
  private probeDone = false
  private readonly opts: Required<Omit<LiveSocketOptions, 'factory' | 'probe'>> & { factory: (url: string) => WebSocket; probe?: LiveSocketOptions['probe'] }
  state: LinkState = 'closed'
  /** Сервер отказал: открыто слишком много вкладок дашборда (по ответу /api/link после неудачного подключения). Снимается при успешном подключении. */
  full = false

  constructor(opts: LiveSocketOptions) {
    this.opts = {
      backoffBaseMs: 1000,
      backoffMaxMs: 15000,
      silenceMs: 40000,
      random: Math.random,
      factory: (url) => new WebSocket(url),
      ...opts,
    }
  }

  /**
   * Подписка на тему. Сервер шлёт снимок один раз при подключении и дальше только изменения, а виджеты страниц подписываются позже (при заходе на страницу):
   * поэтому новому подписчику сразу отдаётся последнее полученное состояние темы. Разовые ответы (EPHEMERAL) не повторяются.
   */
  subscribe(topic: string, handler: MessageHandler): () => void {
    let set = this.handlers.get(topic)
    if (!set) this.handlers.set(topic, (set = new Set()))
    set.add(handler)
    const last = this.last.get(topic)
    if (last) handler(last)
    return () => set.delete(handler)
  }

  onState(handler: StateHandler): () => void {
    this.stateHandlers.add(handler)
    handler(this.state)
    return () => this.stateHandlers.delete(handler)
  }

  onRefusal(handler: RefusalHandler): () => void {
    this.refusalHandlers.add(handler)
    handler(this.full)
    return () => this.refusalHandlers.delete(handler)
  }

  /** Отправить команду серверу ({"op": ...}). false: сокет не открыт (команда не буферизуется; состояние после переподключения восстанавливает вызывающий по onState). */
  send(payload: unknown): boolean {
    if (!this.ws || this.state !== 'open') return false
    this.ws.send(JSON.stringify(payload))
    return true
  }

  start(): void {
    if (!this.stopped) return
    this.stopped = false
    this.connect()
  }

  stop(): void {
    this.stopped = true
    this.clearTimers()
    const ws = this.ws
    this.ws = null
    if (ws) {
      ws.onopen = ws.onmessage = ws.onclose = ws.onerror = null
      ws.close(1000)      // нормальное закрытие: сервер освобождает слот сразу, не ждёт ping_timeout
    }
    this.setState('closed')
  }

  private setFull(v: boolean): void {
    if (this.full === v) return
    this.full = v
    this.refusalHandlers.forEach((h) => h(v))
  }

  /** После неудачного подключения (сокет не открывался): один вопрос серверу, не отказ ли это по числу вкладок. */
  private async checkRefusal(): Promise<void> {
    const probe = this.opts.probe
    if (!probe || this.probeDone || this.probes >= PROBE_MAX) return
    this.probes++
    let r: { clients: number; max: number } | null = null
    try {
      r = await probe()
    } catch {
      r = null
    }
    if (this.stopped || this.state === 'open') return
    if (r && Number.isFinite(r.clients) && Number.isFinite(r.max)) {
      this.probeDone = true
      this.setFull(r.clients >= r.max)
    }
  }

  /** Задержка перед следующей попыткой (с разбросом ±30 %). Вынесено для проверки в тестах. */
  backoffMs(attempt: number): number {
    const base = Math.min(this.opts.backoffMaxMs, this.opts.backoffBaseMs * 2 ** Math.min(attempt, 10))
    return Math.max(0, base * (1 + 0.3 * (2 * this.opts.random() - 1)))
  }

  private setState(s: LinkState): void {
    if (this.state === s) return
    this.state = s
    this.stateHandlers.forEach((h) => h(s))
  }

  private clearTimers(): void {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer)
    if (this.watchdog) clearInterval(this.watchdog)
    this.reconnectTimer = this.watchdog = null
  }

  private connect(): void {
    this.setState('connecting')
    let ws: WebSocket
    try {
      ws = this.opts.factory(this.opts.url)
    } catch {
      this.scheduleReconnect()
      return
    }
    this.ws = ws
    let opened = false
    ws.onopen = () => {
      opened = true
      this.attempt = 0
      this.probes = 0
      this.probeDone = false
      this.setFull(false)
      this.lastRx = Date.now()
      this.setState('open')
      this.watchdog = setInterval(() => {
        if (Date.now() - this.lastRx > this.opts.silenceMs) ws.close()
      }, Math.max(1000, this.opts.silenceMs / 4))
    }
    ws.onmessage = (ev: MessageEvent) => {
      this.lastRx = Date.now()
      let m: LiveMessage
      try {
        m = JSON.parse(String(ev.data)) as LiveMessage
      } catch {
        return
      }
      if (!m || typeof m.topic !== 'string') return
      if (!EPHEMERAL.has(m.topic)) this.last.set(m.topic, m)
      this.handlers.get(m.topic)?.forEach((h) => h(m))
    }
    ws.onclose = () => {
      if (this.ws !== ws) return
      this.ws = null
      if (this.watchdog) clearInterval(this.watchdog)
      this.watchdog = null
      this.setState('closed')
      if (!this.stopped) {
        if (!opened) void this.checkRefusal()
        this.scheduleReconnect()
      }
    }
    ws.onerror = () => {
      /* за ошибкой следует close: переподключение запускается там */
    }
  }

  private scheduleReconnect(): void {
    if (this.stopped || this.reconnectTimer) return
    const delay = this.backoffMs(this.attempt++)
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null
      if (!this.stopped) this.connect()
    }, delay)
  }
}

/** Лёгкий запрос «сколько вкладок занято» того же хоста: GET /api/link -> {clients, max}. Без данных платформы. */
export async function fetchLinkInfo(f: typeof fetch = fetch): Promise<{ clients: number; max: number } | null> {
  const r = await f('/api/link', { cache: 'no-store', credentials: 'omit' })
  if (!r.ok) return null
  const j = (await r.json()) as { clients?: unknown; max?: unknown }
  return typeof j.clients === 'number' && typeof j.max === 'number' ? { clients: j.clients, max: j.max } : null
}

/** Адрес сокета того же хоста, с которого открыта страница (на dev-сервере Vite проксируется на ./tb dash2). */
export function defaultSocketUrl(loc: Pick<Location, 'protocol' | 'host'> = window.location): string {
  return `${loc.protocol === 'https:' ? 'wss' : 'ws'}://${loc.host}/ws`
}
