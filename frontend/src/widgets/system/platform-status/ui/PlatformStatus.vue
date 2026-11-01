<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { UiStatusDot } from '@/shared/ui'
import { usePlatformStatus } from '../model/use-platform-status'
import type { StatusLevel } from '../lib/types'

/**
 * Один индикатор состояния платформы: цвет и короткий текст по самой серьёзной проблеме, по клику окно со всеми статусами (связь, исполнитель, биржа, мозг, данные рынка, новости,
 * проверки запуска). Окно: role=dialog рядом с кнопкой, Esc закрывает и возвращает фокус на кнопку, клик снаружи и уход фокуса закрывают. Данные только из WebSocket.
 * Движение: прозрачность и смещение окна 200 мс (кривая из motion.css); при prefers-reduced-motion только прозрачность.
 */
const { summary } = usePlatformStatus()
const DIALOG_ID = 'platform-status-dialog'
const WORD: Record<StatusLevel, string> = { ok: 'в норме', warn: 'внимание', bad: 'проблема', idle: 'не активно' }

const open = ref(false)
const root = ref<HTMLElement | null>(null)
const btn = ref<HTMLButtonElement | null>(null)
const pop = ref<HTMLElement | null>(null)
const pos = ref<Record<string, string>>({})

const label = computed(() => `Состояние платформы: ${summary.value.text}${summary.value.extra ? `, и ещё ${summary.value.extra}` : ''}. ${open.value ? 'Закрыть' : 'Открыть'} подробности`)
const sub = computed(() => (summary.value.problems ? `Проблем: ${summary.value.problems}` : summary.value.level === 'idle' ? summary.value.text : 'Проблем нет'))

function place(): void {
  const b = btn.value
  if (!b) return
  const r = b.getBoundingClientRect()
  const vw = document.documentElement.clientWidth
  const width = Math.min(460, vw - 16)
  const left = Math.min(Math.max(8, r.right - width), vw - width - 8)
  const top = r.bottom + 8
  pos.value = { top: `${top}px`, left: `${left}px`, width: `${width}px`, maxHeight: `${Math.max(160, window.innerHeight - top - 8)}px`, transformOrigin: `${Math.round(r.right - left)}px 0` }
}

function close(refocus: boolean): void {
  if (!open.value) return
  open.value = false
  if (refocus) void nextTick(() => btn.value?.focus())
}
function toggle(): void {
  if (open.value) return close(false)
  place()
  open.value = true
  void nextTick(() => pop.value?.focus({ preventScroll: true }))
}

function onKey(e: KeyboardEvent): void {
  if (e.key === 'Escape') close(true)
}
function onPointer(e: PointerEvent): void {
  if (root.value && !root.value.contains(e.target as Node)) close(false)
}
function onFocusOut(e: FocusEvent): void {
  const to = e.relatedTarget as Node | null
  if (open.value && to && root.value && !root.value.contains(to)) close(false)
}

watch(open, (v) => {
  if (!v) return unbind()
  document.addEventListener('keydown', onKey)
  document.addEventListener('pointerdown', onPointer, true)
  window.addEventListener('resize', place)
  window.addEventListener('scroll', place, true)
})
function unbind(): void {
  document.removeEventListener('keydown', onKey)
  document.removeEventListener('pointerdown', onPointer, true)
  window.removeEventListener('resize', place)
  window.removeEventListener('scroll', place, true)
}
onUnmounted(unbind)
</script>

<template>
  <div ref="root" class="ps" @focusout="onFocusOut">
    <button ref="btn" type="button" class="ind" :aria-expanded="open" :aria-controls="DIALOG_ID" aria-haspopup="dialog" :aria-label="label" @click="toggle">
      <UiStatusDot :state="summary.level" aria-hidden="true" />
      <span class="txt" :class="`lv-${summary.level}`">{{ summary.text }}</span>
      <span v-if="summary.extra" class="more" aria-hidden="true">+{{ summary.extra }}</span>
    </button>
    <Transition name="pop">
      <div v-if="open" :id="DIALOG_ID" ref="pop" class="pop" role="dialog" aria-label="Состояние платформы" tabindex="-1" :style="pos">
        <header class="head">
          <div class="ttl">
            <h2>Состояние платформы</h2>
            <p class="sub" :class="`lv-${summary.level}`">{{ sub }}</p>
          </div>
          <button type="button" class="x" aria-label="Закрыть окно состояния" @click="close(true)">
            <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M3.5 3.5l9 9M12.5 3.5l-9 9" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" /></svg>
          </button>
        </header>
        <div class="body">
          <section v-for="g in summary.groups" :key="g.id" class="grp" :aria-label="g.label">
            <h3>{{ g.label }}</h3>
            <ul>
              <li v-for="r in g.rows" :key="r.id" class="row">
                <UiStatusDot :state="r.level" aria-hidden="true" />
                <span class="name">{{ r.label }}<span class="sr">, {{ WORD[r.level] }}</span></span>
                <span class="val">{{ r.value }}</span>
                <span v-if="r.reason" class="why" :class="`lv-${r.level}`">{{ r.reason }}</span>
              </li>
            </ul>
          </section>
          <p v-if="!summary.groups.length" class="empty">Данные платформы ещё не пришли.</p>
        </div>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.ps { position: relative; min-width: 0; max-width: 100%; }
.ind { display: inline-flex; align-items: center; gap: var(--space-2); min-height: 36px; max-width: 100%; padding: 0 var(--space-3); background: transparent; color: var(--text); border: 1px solid var(--line); border-radius: var(--radius-sm); font-size: 13px; cursor: pointer; transition: background-color var(--dur-fast) ease, border-color var(--dur-fast) ease; }
.ind[aria-expanded="true"] { background: var(--panel2); border-color: var(--line2); }
.txt { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.txt.lv-warn { color: var(--warn); }
.txt.lv-bad { color: var(--neg); }
.txt.lv-idle { color: var(--muted); }
.more { flex: none; font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
@media (hover: hover) and (pointer: fine) {
  .ind:hover { background: var(--panel2); }
  .x:hover { background: var(--panel2); color: var(--text); }
}

.pop { position: fixed; z-index: 55; display: flex; flex-direction: column; min-height: 0; background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); box-shadow: var(--lift); outline: none; }
.head { flex: none; display: flex; align-items: flex-start; justify-content: space-between; gap: var(--space-3); padding: var(--space-4) var(--space-4) var(--space-3); border-bottom: 1px solid var(--line); }
.ttl h2 { margin: 0; font-size: 15px; font-weight: 600; }
.sub { margin: 2px 0 0; font-size: 12px; color: var(--muted); }
.sub.lv-warn { color: var(--warn); }
.sub.lv-bad { color: var(--neg); }
.x { flex: none; display: grid; place-items: center; width: 36px; height: 36px; margin: -6px -6px 0 0; background: transparent; border: 0; border-radius: var(--radius-sm); color: var(--muted); cursor: pointer; transition: background-color var(--dur-fast) ease, color var(--dur-fast) ease; }
.body { flex: 1 1 auto; min-height: 0; overflow-y: auto; padding: var(--space-2) var(--space-4) var(--space-4); overscroll-behavior: contain; }
.grp { padding-top: var(--space-3); }
.grp h3 { margin: 0 0 var(--space-1); font-size: 12px; font-weight: 600; color: var(--muted); }
.grp ul { list-style: none; margin: 0; padding: 0; }
.row { display: grid; grid-template-columns: 10px minmax(9em, 1fr) auto; column-gap: var(--space-3); align-items: baseline; padding: var(--space-2) 0; border-top: 1px solid var(--line); font-size: 13px; }
.grp li:first-child { border-top: 0; }
.row > :first-child { align-self: center; }
.name { min-width: 0; overflow-wrap: break-word; }
.val { max-width: 22em; text-align: right; text-wrap: balance; color: var(--muted); font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
@media (max-width: 520px) {
  .row { grid-template-columns: 10px minmax(0, 1fr); }
  .val { grid-column: 2; max-width: none; text-align: left; text-wrap: pretty; }
}
.why { grid-column: 2 / -1; margin-top: 2px; font-size: 12px; color: var(--muted); overflow-wrap: anywhere; }
.why.lv-warn { color: var(--warn); }
.why.lv-bad { color: var(--neg); }
.empty { margin: var(--space-4) 0 0; font-size: 13px; color: var(--muted); }
.sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }

.pop-enter-active { transition: opacity var(--dur-enter) var(--ease-out), transform var(--dur-enter) var(--ease-out); }
.pop-leave-active { transition: opacity 120ms var(--ease-out); }
.pop-enter-from { opacity: 0; transform: translateY(-4px) scale(0.98); }
.pop-leave-to { opacity: 0; }
@media (prefers-reduced-motion: reduce) {
  .pop-enter-from { transform: none; }
}
</style>
