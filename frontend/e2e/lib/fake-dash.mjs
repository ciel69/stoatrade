/**
 * Фейковый сервер dash2: статика frontend/dist и WebSocket /ws на одном свободном порту 127.0.0.1.
 * Эмулирует протокол из src/tb/dash2/server.py: при подключении `hello` и снимок каждой темы, дальше изменения {topic, seq, ts, data}, пульс `link`,
 * ответы на операции (ping, mode, ctl, plan, brief, day_report, usage, exp). К реальной платформе, портам 8766/8767 и файлам state/ не обращается.
 */
import http from 'node:http'
import { createReadStream, existsSync, statSync } from 'node:fs'
import path from 'node:path'
import { WebSocketServer } from 'ws'
import { DIST_DIR } from './env.mjs'

const MIME = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.mjs': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8',
  '.svg': 'image/svg+xml', '.json': 'application/json; charset=utf-8', '.woff2': 'font/woff2', '.png': 'image/png', '.ico': 'image/x-icon', '.map': 'application/json',
}

/** Режимные темы сервер строит отдельно для каждого режима клиента; фейк режимов не различает и всегда отдаёт одни и те же данные. */
export const MODE_TOPICS = ['home', 'trades', 'decisions', 'models', 'plans', 'ideas']

export class FakeDash {
  /**
   * @param {object} o
   * @param {Record<string, unknown>} o.topics начальные данные тем
   * @param {object} o.hello содержимое hello.ctl
   * @param {number} [o.heartbeatMs] период пульса `link` (0: выключен)
   */
  constructor({ topics, hello, heartbeatMs = 0 }) {
    this.state = new Map()
    this.seq = new Map()
    for (const [k, v] of Object.entries(topics)) this.state.set(k, v)
    this.hello = hello
    this.heartbeatMs = heartbeatMs
    this.sockets = new Set()
    this.connects = 0
    /** Сколько пульсов `link` отправлено. */
    this.pulses = 0
    /** Все сообщения клиента (разобранные), для проверок. */
    this.received = []
    /** Все HTTP-запросы к статике (метод + путь). */
    this.httpRequests = []
    /** true: новые подключения отклоняются на рукопожатии (имитация недоступного сервера). */
    this.blockConnections = false
    /** Правила команд управления по действию, см. ctl-rules.mjs и сценарии. */
    this.rules = {}
    /** Ответы на разовые запросы (plan_detail и т.п.): op -> (msg) => data. */
    this.replies = {}
    this._timers = new Set()
    this.server = http.createServer((req, res) => this._http(req, res))
    this.wss = new WebSocketServer({
      noServer: true,
      verifyClient: (_info, cb) => (this.blockConnections ? cb(false, 503, 'blocked') : cb(true)),
    })
    this.server.on('upgrade', (req, socket, head) => {
      if ((req.url || '').split('?')[0] !== '/ws') {
        socket.destroy()
        return
      }
      this.wss.handleUpgrade(req, socket, head, (ws) => this._onConnection(ws))
    })
  }

  async start() {
    await new Promise((resolve) => this.server.listen(0, '127.0.0.1', resolve))
    this.port = this.server.address().port
    this.url = `http://127.0.0.1:${this.port}`
    if (this.heartbeatMs > 0) {
      const t = setInterval(() => { this.pulses += 1; this.broadcast('link', { clients: this.sockets.size }) }, this.heartbeatMs)
      this._timers.add(t)
    }
    return this
  }

  async stop() {
    for (const t of this._timers) { clearInterval(t); clearTimeout(t) }
    this._timers.clear()
    for (const ws of this.sockets) ws.terminate()
    await new Promise((resolve) => this.wss.close(resolve))
    this.server.closeAllConnections?.()
    await new Promise((resolve) => this.server.close(resolve))
  }

  // ---- статика с запасным вариантом SPA (без CSP и проверки Host: это тестовый сервер)
  _http(req, res) {
    const pathname = decodeURIComponent((req.url || '/').split('?')[0])
    this.httpRequests.push(`${req.method} ${pathname}`)
    let file = path.normalize(path.join(DIST_DIR, pathname === '/' ? 'index.html' : pathname))
    const inside = file.startsWith(DIST_DIR)
    if (!inside || !existsSync(file) || !statSync(file).isFile()) {
      if (path.extname(pathname)) { res.writeHead(404).end('not found'); return }
      file = path.join(DIST_DIR, 'index.html')
    }
    res.writeHead(200, { 'Content-Type': MIME[path.extname(file)] || 'application/octet-stream', 'Cache-Control': 'no-store' })
    createReadStream(file).pipe(res)
  }

  // ---- сообщения
  _msg(topic, data, seqOverride) {
    let seq = seqOverride
    if (seq === undefined) {
      seq = (this.seq.get(topic) ?? 0)
    }
    return JSON.stringify({ topic, seq, ts: Date.now() / 1000, data })
  }

  _snapshot(ws) {
    ws.send(this._msg('hello', {
      topics: [...this.state.keys()].sort(), tick_s: 1, heartbeat_s: 15, server_ts: Date.now() / 1000, mode: 'paper', modes: ['paper', 'live', 'testnet', 'all'], ctl: this.hello,
    }, 0))
    for (const [topic] of this.state) ws.send(this._msg(topic, this.state.get(topic)))
  }

  _onConnection(ws) {
    this.connects += 1
    this.sockets.add(ws)
    this._snapshot(ws)
    ws.on('message', (raw) => {
      let m
      try { m = JSON.parse(String(raw)) } catch { return }
      if (!m || typeof m !== 'object') return
      this.received.push(m)
      void this._op(ws, m)
    })
    ws.on('close', () => this.sockets.delete(ws))
    ws.on('error', () => this.sockets.delete(ws))
  }

  /** Отправить сообщение одному соединению (разовые ответы: seq 0, как у сервера). */
  reply(ws, topic, data) {
    if (ws.readyState === 1) ws.send(this._msg(topic, data, 0))
  }

  /** Записать тему и разослать всем. */
  set(topic, data) {
    this.state.set(topic, data)
    this.seq.set(topic, (this.seq.get(topic) ?? 0) + 1)
    const msg = this._msg(topic, data)
    for (const ws of this.sockets) if (ws.readyState === 1) ws.send(msg)
  }

  /** Изменить тему функцией (получает копию, возвращает новое значение или меняет на месте). */
  patch(topic, fn) {
    const cur = structuredClone(this.state.get(topic) ?? {})
    const next = fn(cur) ?? cur
    this.set(topic, next)
  }

  broadcast(topic, data) {
    const msg = this._msg(topic, data, 0)
    for (const ws of this.sockets) if (ws.readyState === 1) ws.send(msg)
  }

  /** Оборвать все соединения без закрывающего кадра (как падение сети или сервера). */
  dropAll() {
    for (const ws of this.sockets) ws.terminate()
    this.sockets.clear()
  }

  later(ms, fn) {
    const t = setTimeout(() => { this._timers.delete(t); fn() }, ms)
    this._timers.add(t)
  }

  /** Сообщения клиента с заданной операцией. */
  ops(op) {
    return this.received.filter((m) => m.op === op)
  }

  // ---- операции клиента
  async _op(ws, m) {
    switch (m.op) {
      case 'ping': return this.reply(ws, 'pong', {})
      case 'mode': return MODE_TOPICS.forEach((t) => this.state.has(t) && ws.send(this._msg(t, this.state.get(t))))
      case 'ctl': return this._ctl(ws, m)
      default: {
        const fn = this.replies[m.op]
        if (fn) {
          const res = await fn(m)
          if (res) this.reply(ws, res.topic, res.data)
        }
      }
    }
  }

  _ctlResult(ws, m, patch) {
    this.reply(ws, 'ctl_result', { id: m.id, kind: m.kind, action: m.action, ok: true, code: 'OK', msg: '', ...patch })
  }

  /**
   * Команда управления. Для действия ищется правило this.rules[action]:
   *   challenge(args, msg) -> { ok?, code?, msg?, data? } | 'silence'     (по умолчанию безрисковое условие без предупреждений)
   *   command(args, msg, api) -> { accept?: {ok, code, msg}, final?: {status, code, msg, data} | 'silence', delayMs? }
   * Ответы приходят асинхронно, как у настоящего сервера (через очередь ctl).
   */
  _ctl(ws, m) {
    const rule = this.rules[m.action] ?? {}
    if (m.kind === 'challenge') {
      const out = rule.challenge ? rule.challenge(m.args ?? {}, m) : { data: calmChallenge() }
      if (out === 'silence') return
      this.later(rule.challengeDelayMs ?? 30, () => this._ctlResult(ws, m, { kind: 'challenge', ...out }))
      return
    }
    const out = rule.command ? rule.command(m.args ?? {}, m, this) : { final: { status: 'done', code: 'OK', msg: 'Готово.' } }
    const accept = out.accept ?? { ok: true, code: 'OK', msg: 'принято' }
    this.later(30, () => {
      this._ctlResult(ws, m, { kind: 'command', ...accept })
      if (!accept.ok || out.final === 'silence' || !out.final) return
      this.later(out.delayMs ?? 150, () => {
        if (out.apply) out.apply(this)
        this.reply(ws, 'ctl_final', { id: 'q-' + m.id, rid: m.id, data: null, ...out.final })
      })
    })
  }
}

/** Безрисковое условие: проверки пройдены, предупреждений нет (действие уходит сразу, без диалога). */
export function calmChallenge(extra = {}) {
  return {
    gates: [{ id: 'G1', name: 'Исполнитель жив', ok: true }, { id: 'G2', name: 'Сверка свежая', ok: true, msg: '4 с назад' }],
    warnings: [], pre_amber: [], danger: false, phrases: [], blocked: false, nonce: 'n-calm', min_delay: 0, expires_in: 120, ...extra,
  }
}
