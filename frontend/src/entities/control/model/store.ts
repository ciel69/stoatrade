import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { useLive, type LiveMessage } from '@/shared/api'
import { failureText, liveRefusal } from '@/shared/lib'
import { buttonKind, dayArgs } from '../lib/flow'
import type { ChallengeData, CtlHello, CtlResult, CtlSnapshot, FlowPhase, StartMode } from './types'

/** Пример для экранов отладки: id дня в формате платформы, чтобы подпись читалась как «2 ноября», а не «торговый день». */
const MOCK_DAY = 'D-20261102-1'

/**
 * Состояние управления днём: тема `ctl`, hello.ctl (csrf соединения) и запрос условий запуска для выбранного режима (проверки окна запуска).
 * Сами команды (запуск и остановка дня) идут через общий движок действий entities/action, здесь их нет. Режимы paper, testnet, observe доступны всегда; live только в расширенном режиме и при выключателе владельца (иначе сервер отвечает E_ELEVATE или E_DISABLED).
 */
export const useControlStore = defineStore('control', () => {
  const ctl = ref<CtlSnapshot | null>(null)
  const hello = ref<CtlHello>({ enabled: false })
  /** Окно запуска и остановки дня открыто: одно на приложение, открыть его можно и с кнопки в колонке, и со страницы «Управление». */
  const dialogOpen = ref(false)

  const phase = ref<FlowPhase>('idle')
  const mode = ref<StartMode>('paper')
  const challenge = ref<ChallengeData | null>(null)
  const error = ref<string | null>(null)
  const pending = ref<string | null>(null)        // id нашего запроса условий, ответ на который ждём
  let seq = 0
  let started = false

  const button = computed(() => buttonKind(ctl.value, hello.value.enabled))

  let mocked = false

  function applyCtl(m: LiveMessage): void {
    if (mocked) return
    ctl.value = m.data as CtlSnapshot
    // платформа уже запущена не нами (или после перезагрузки страницы): подстраиваем фазу под реальное состояние
    const st = ctl.value.platform.state
    if (st === 'starting' && phase.value !== 'starting') phase.value = 'starting'
    else if (st !== 'starting' && phase.value === 'starting') phase.value = 'idle'        // запуск кончился (успехом или нет): само окно покажет по состоянию платформы
  }

  function applyHello(m: LiveMessage): void {
    if (mocked) return
    const d = (m.data as { ctl?: CtlHello }).ctl
    hello.value = d && typeof d.enabled === 'boolean' ? d : { enabled: false }
  }

  /** Здесь только ответ на запрос условий запуска (challenge). Сами команды и их итоги ведёт общий движок entities/action. */
  function applyResult(m: LiveMessage): void {
    const r = m.data as CtlResult
    if (r.id !== pending.value) return
    pending.value = null
    challenge.value = r.data ?? null
    if (r.code === 'OK') phase.value = 'ready'
    else if (r.code === 'E_GATE' || r.code === 'E_STATE') {
      phase.value = 'blocked'
      error.value = failureText(r.code, r.msg)
    } else {
      phase.value = 'failed'
      error.value = (mode.value === 'live' ? liveRefusal(r.code) : null) ?? failureText(r.code, r.msg)        // реальные деньги: «выключено владельцем» и «нужен расширенный режим» говорят прямо
    }
  }

  function start(): void {
    if (started) return
    started = true
    const { socket } = useLive()
    socket.subscribe('hello', applyHello)
    socket.subscribe('ctl', applyCtl)
    socket.subscribe('ctl_result', applyResult)
  }

  function sendChallenge(action: string, args: Record<string, unknown>): boolean {
    if (!hello.value.enabled || !hello.value.csrf) {
      error.value = 'Управление из этого дашборда недоступно.'
      phase.value = 'failed'
      return false
    }
    const id = `c${++seq}`
    const ok = useLive().socket.send({ op: 'ctl', id, csrf: hello.value.csrf, kind: 'challenge', action, args })
    if (!ok) {
      error.value = failureText('E_LINK')
      phase.value = 'failed'
      return false
    }
    pending.value = id
    return true
  }

  /** Выбран режим: спрашиваем у ctl условия старта (проверки, предупреждения). Ничего не запускает. */
  function check(next: StartMode): void {
    if (mocked) return
    mode.value = next
    challenge.value = null
    error.value = null
    phase.value = 'checking'
    sendChallenge('day.start', dayArgs(next))
  }

  function reset(): void {
    phase.value = 'idle'
    challenge.value = null
    error.value = null
    pending.value = null
  }

  /** Только для экрана-примера (?start=mock): подставляет ход запуска без сервера. */
  function mock(kind: 'launch' | 'pick' | 'done' = 'launch'): void {
    mocked = true
    hello.value = { enabled: true, csrf: 'mock' }
    const launch = kind === 'launch'
    ctl.value = {
      alive: true, configured: true, platform: { state: launch ? 'starting' : kind === 'done' ? 'running' : 'stopped', mode: 'paper', session: 'day', health: 'green', day_id: launch ? null : MOCK_DAY, trading: kind === 'done', observe: false },
      step: launch ? { name: 'day' } : null, kill: null, risk: { level: 3, name: 'Базовый' }, busy: launch, checks: [], banner: null,
      day: { day_id: MOCK_DAY, state: launch ? 'starting' : 'running', mode: 'paper', steps: (launch ? ['S1', 'S2', 'S3', 'S4', 'S5'] : kind === 'done' ? ['S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7', 'S8', 'S9'] : []).map((name) => ({ name, phase: 'start', ok: true, code: null })) },
    }
    mode.value = 'paper'
    if (kind === 'pick') {
      challenge.value = {
        gates: [{ id: 'H1', name: 'Чистый KILL', ok: true }, { id: 'H3', name: 'Связь', ok: true, msg: 'исполнитель жив, сверка свежая' }, { id: 'H6', name: 'Мозг доступен', ok: true }],
        warnings: [{ id: 'W1', code: 'W_PAPER_HISTORY', msg: 'Мало paper-истории: рекомендуется 30 сделок и 7 дней' }], blocked: false,
      }
      phase.value = 'ready'
    } else {
      phase.value = launch ? 'starting' : 'done'
    }
  }

  /**
   * Отладка запуска на реальные деньги (?start=mock-live|mock-live-blocked|mock-live-off): live — все проверки пройдены, есть предупреждения; blocked — не пройдено H5 и H6; off — выключатель владельца выключен.
   * Расширенное управление включено, платформа остановлена; на сервер ничего не уходит.
   */
  function mockLive(kind: 'live' | 'live-blocked' | 'live-off'): void {
    mock('pick')
    hello.value = { enabled: true, csrf: 'mock', full: true, modes: ['day.start', 'day.stop'] }
    if (ctl.value) ctl.value = { ...ctl.value, live_start: kind !== 'live-off', risk: { level: 3, name: 'Базовый' } }
    mode.value = 'live'
    if (kind === 'live-off') {
      challenge.value = null
      phase.value = 'idle'
      return
    }
    const bad = kind === 'live-blocked'
    challenge.value = {
      danger: true, blocked: bad, phrases: ['ТОРГУЮ LIVE 2026-11-02 УРОВЕНЬ 3'], nonce: 'mock',
      gates: [
        { id: 'H1', name: 'KILL чист', ok: true, msg: 'state/KILL нет' },
        { id: 'H2', name: 'Стоп на бирже у каждой позиции', ok: true, deferred: true, msg: 'проверится после переключения исполнителя на live' },
        { id: 'H3', name: 'Связь с биржей и исполнителем', ok: true, deferred: true, msg: 'исполнитель жив, режим будет переключён на live; связь перепроверится после переключения' },
        { id: 'H4', name: 'Сверка портфеля с биржей', ok: true, deferred: true, msg: 'проверится после переключения исполнителя на live' },
        bad ? { id: 'H5', name: 'Суточный лимит убытка', ok: false, msg: 'убыток за сутки 3.20 из 3.00 USDT' } : { id: 'H5', name: 'Суточный лимит убытка', ok: true, msg: 'убыток за сутки 0.40 из 3.00 USDT' },
        bad ? { id: 'H6', name: 'Мозг доступен', ok: false, msg: 'нет heartbeat сервиса brain' } : { id: 'H6', name: 'Мозг доступен', ok: true, msg: 'мозг отвечает (hb 2 с)' },
        { id: 'H7', name: 'Конфигурации читаются и не ослабляют защиту', ok: true, msg: 'конфигурации в порядке, уровень риска 3' },
        { id: 'H8', name: 'Лимит запусков live', ok: true, msg: 'запусков за сутки 1 из 3' },
      ],
      warnings: bad ? [] : [{ id: 'W1', code: 'W_PAPER', msg: 'сделок 18 из рекомендованных 30; дней 5 из 7' }, { id: 'W9', code: 'W_SESSION', msg: 'сделок в журнале 4 из 10' }],
    }
    phase.value = bad ? 'blocked' : 'ready'
    if (bad) error.value = failureText('E_GATE')
  }

  /** Отладка настроек (?edit=mock): платформа остановлена (running=false, режим paper) или идёт (режим testnet), уровень риска 3. Только в mocked-режиме. */
  let platformMock: boolean | null = null
  function mockPlatform(running: boolean): void {
    if (platformMock === running) return        // повторный вызов (блок и панель) не сбрасывает уже «применённые» значения
    platformMock = running
    mock(running ? 'done' : 'pick')
    if (ctl.value) ctl.value = { ...ctl.value, platform: { ...ctl.value.platform, mode: running ? 'testnet' : 'paper' } }
  }

  /** Отладка снятия аварийной остановки (?kill=mock|mock-live|mock-blocked|mock-off): платформа остановлена аварийно, KILL активен (live: режим live). off: кнопка выключена в настройках платформы. */
  function mockKill(live: boolean, off = false): void {
    mock('pick')
    if (!ctl.value) return
    ctl.value = {
      ...ctl.value, kill: { flatten: false, reason: 'ручная остановка' }, clear_kill: !off,
      platform: { ...ctl.value.platform, state: 'halted', mode: live ? 'live' : 'paper', day_id: null, trading: false, health: 'red' },
    }
  }

  /** Отладка: через несколько секунд после снятия «сервер» убирает KILL, платформа остановлена (так обновляется тема ctl). */
  function mockKillCleared(delayMs = 2500): void {
    if (!mocked) return
    setTimeout(() => {
      if (!ctl.value) return
      ctl.value = { ...ctl.value, kill: null, platform: { ...ctl.value.platform, state: 'stopped', health: 'green' } }
    }, delayMs)
  }

  /** Отладка: «сервер» применяет смену уровня или режима через несколько секунд, как обновляется тема ctl. */
  function mockApply(p: { level?: number; mode?: string }, delayMs = 2500): void {
    if (!mocked) return
    setTimeout(() => {
      if (!ctl.value) return
      ctl.value = {
        ...ctl.value,
        risk: p.level !== undefined ? { level: p.level, name: null } : ctl.value.risk,
        platform: p.mode !== undefined ? { ...ctl.value.platform, mode: p.mode } : ctl.value.platform,
      }
    }, delayMs)
  }

  /** Открыть окно: показывает текущее состояние, а не прошлый итог. */
  function openDialog(): void {
    if (phase.value === 'done' || phase.value === 'failed') reset()
    dialogOpen.value = true
  }

  return { ctl, hello, dialogOpen, openDialog, phase, mode, challenge, error, pending, button, start, check, reset, mock, mockLive, mockPlatform, mockApply, mockKill, mockKillCleared, applyCtl, applyHello, applyResult }
})
