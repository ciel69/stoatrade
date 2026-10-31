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
const EPHEMERAL = new Set(['pong', 'link', 'plan_detail', 'ctl_result', 'ctl_final'])

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
}

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
  private readonly opts: Required<Omit<LiveSocketOptions, 'factory'>> & { factory: (url: string) => WebSocket }
  state: LinkState = 'closed'

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
      ws.close()
    }
    this.setState('closed')
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
    ws.onopen = () => {
      this.attempt = 0
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
      if (!this.stopped) this.scheduleReconnect()
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

/** Адрес сокета того же хоста, с которого открыта страница (на dev-сервере Vite проксируется на ./tb dash2). */
export function defaultSocketUrl(loc: Pick<Location, 'protocol' | 'host'> = window.location): string {
  return `${loc.protocol === 'https:' ? 'wss' : 'ws'}://${loc.host}/ws`
}
