import { computed, onUnmounted, ref, watch } from 'vue'
import { useActionStore } from '@/entities/action'
import {
  addFallback, changeLines, changed, chainArgs, chainArgsOf, chainOf, liveLocked, moveFallback, overlay, reflects, removeFallback, reverseLines, roleLabel, setPrimary, targetOf, useArgs, useRoleStore, whatText,
  type Chain, type ChangeLine, type ProfileLike,
} from '@/entities/role'

/** Ждём, пока тема settings отразит изменение: она обновляется до 15 с, дольше 25 с не держим «ожидаем». */
export const WAIT_MS = 25_000
const KEEP = 4

export interface AppliedEntry {
  id: number
  kind: 'change' | 'revert'
  lines: ChangeLine[]
  prev: Partial<Chain>
  next: Partial<Chain>
  via: 'chain' | 'use'
  /** wait: ждём обновления данных; seen: снимок отразил; late: 25 с прошло, данные ещё не пришли */
  state: 'wait' | 'seen' | 'late'
  reverted: boolean
}

const sameIds = (a: readonly string[], b: readonly string[]) => a.length === b.length && a.every((x, i) => x === b[i])

/**
 * Редактирование моделей роли в панели. Черновик хранится как отличия от текущей цепочки (основная и запасные), поэтому свежий снимок его не ломает.
 * Применение идёт через общий движок действий (challenge и диалог «Принимаю риски» решает сервер). После успеха ждём, пока снимок отразит изменение;
 * пока ждём, «текущая» цепочка считается с учётом отправленного, чтобы «Применить» не загоралась снова.
 */
export function useRoleEdit(roleName: () => string) {
  const store = useRoleStore()
  const action = useActionStore()
  const snap = computed(() => store.snapshot)
  const row = computed(() => snap.value?.roles.find((x) => x.role === roleName()) ?? null)
  const profiles = computed(() => snap.value?.profile_list ?? [])
  const target = computed(() => (row.value ? targetOf(row.value) : ''))
  const locked = computed(() => !!row.value && liveLocked(row.value, snap.value?.mode, snap.value?.live_team))
  /** роль влияет на live и платформа в live: у профилей без допуска к live показываем предупреждение */
  const liveRelevant = computed(() => snap.value?.mode === 'live' && !!row.value?.live_risk)
  const canEdit = computed(() => (locked.value ? action.can('brain.use') : action.can('brain.role')))
  /** идёт действие (проверка, подтверждение, отправка, ожидание): второе не запускаем */
  const busy = computed(() => action.active)

  const names = computed(() => {
    const m = new Map<string, ProfileLike>()
    for (const p of profiles.value) m.set(p.id, p)
    const r = row.value
    if (r) for (const x of [r.primary, ...r.fallback]) if (x && !m.has(x.id)) m.set(x.id, x)
    return m
  })

  const entries = ref<AppliedEntry[]>([])
  const base = computed<Chain>(() => {
    let c: Chain = row.value ? chainOf(row.value) : { primary: null, fallback: [] }
    for (const e of [...entries.value].reverse()) if (e.state !== 'seen') c = overlay(c, e.next)
    return c
  })

  const dPrimary = ref<string | undefined>()
  const dFallback = ref<string[] | undefined>()
  const draft = computed<Chain>(() => ({ primary: dPrimary.value ?? base.value.primary, fallback: dFallback.value ?? base.value.fallback }))
  const changes = computed(() => changed(base.value, draft.value))
  const lines = computed(() => changeLines(base.value, draft.value, names.value))

  function commit(next: Chain): void {
    dPrimary.value = next.primary !== base.value.primary && next.primary ? next.primary : undefined
    dFallback.value = sameIds(next.fallback, base.value.fallback) ? undefined : [...next.fallback]
  }
  function reset(): void {
    dPrimary.value = undefined
    dFallback.value = undefined
  }
  const pickPrimary = (id: string): void => commit(locked.value ? { ...draft.value, primary: id } : setPrimary(draft.value, id))
  const add = (id: string): void => { if (!locked.value) commit(addFallback(draft.value, id)) }
  const remove = (id: string): void => { if (!locked.value) commit(removeFallback(draft.value, id)) }
  const move = (id: string, dir: -1 | 1): void => { if (!locked.value) commit(moveFallback(draft.value, id, dir)) }

  // ---- применено и ожидание
  let seq = 0
  const timers = new Map<number, ReturnType<typeof setTimeout>>()
  const stop = (id: number): void => { clearTimeout(timers.get(id)); timers.delete(id) }
  const entry = (id: number): AppliedEntry | undefined => entries.value.find((e) => e.id === id)

  function register(spec: Omit<AppliedEntry, 'id' | 'state' | 'reverted'>): void {
    const e: AppliedEntry = { ...spec, id: ++seq, state: reflects(row.value, spec.next) ? 'seen' : 'wait', reverted: false }
    entries.value = [e, ...entries.value].slice(0, KEEP)
    for (const id of [...timers.keys()]) if (!entries.value.some((x) => x.id === id)) stop(id)
    reset()
    if (e.state === 'wait') {
      timers.set(e.id, setTimeout(() => { const x = entry(e.id); if (x && x.state === 'wait') x.state = 'late' }, WAIT_MS))
      if (store.mocked) store.mockApply(target.value, { primary: e.next.primary ?? undefined, fallback: e.next.fallback })
    }
  }
  watch(snap, () => {
    for (const e of entries.value) if (e.state !== 'seen' && reflects(row.value, e.next)) { e.state = 'seen'; stop(e.id) }
  })

  const label = computed(() => roleLabel(roleName()))

  /** Применить черновик одним действием: brain.role (chain) или, для live-роли в live, brain.use с основной. */
  function submit(): boolean {
    if (busy.value || !row.value || !changes.value.any) return false
    const ch = changes.value
    const lk = locked.value
    const args = lk ? (ch.primary && draft.value.primary ? useArgs(target.value, draft.value.primary) : null) : chainArgs(target.value, base.value, draft.value)
    if (!args) return false
    const ls = lk ? lines.value.filter((l) => l.label === 'Основная') : lines.value
    const prev: Partial<Chain> = {}
    const next: Partial<Chain> = {}
    if (ch.primary) { prev.primary = base.value.primary; next.primary = draft.value.primary }
    if (ch.fallback && !lk) { prev.fallback = [...base.value.fallback]; next.fallback = [...draft.value.fallback] }
    const via = lk ? 'use' : 'chain'
    return action.run({
      action: lk ? 'brain.use' : 'brain.role', args, title: `Модель роли «${label.value}»`, what: whatText(ls),
      onDone: () => register({ kind: 'change', lines: ls, prev, next, via }),
    })
  }

  /** Вернуть как было: то же действие с прежними значениями, всё равно через challenge и диалог по правилам сервера. */
  function revert(e: AppliedEntry): boolean {
    if (busy.value || !row.value) return false
    const args = e.via === 'use' ? (e.prev.primary ? useArgs(target.value, e.prev.primary) : null) : chainArgsOf(target.value, e.prev)
    if (!args) return false
    const ls = reverseLines(e.lines)
    return action.run({
      action: e.via === 'use' ? 'brain.use' : 'brain.role', args, title: `Вернуть модели роли «${label.value}»`, what: whatText(ls),
      onDone: () => {
        const x = entry(e.id)
        if (x) x.reverted = true
        register({ kind: 'revert', lines: ls, prev: e.next, next: e.prev, via: e.via })
      },
    })
  }

  onUnmounted(() => { for (const id of [...timers.keys()]) stop(id) })

  return { row, profiles, target, locked, liveRelevant, canEdit, busy, names, entries, base, draft, changes, lines, label, pickPrimary, add, remove, move, reset, submit, revert }
}
