<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { ThoughtBubble, assignRegions, pickThoughts, roleLabel, rolesOfRegion, useBrainStore } from '@/entities/brain'
import { BRAIN_REGIONS, UiBrainIcon, regionOfNode, type BrainIconState } from '@/shared/ui'
import { ageText, usd } from '@/shared/lib'

const brain = useBrainStore()
const now = ref(Date.now() / 1000)
const paused = ref(false)                       // пока курсор или фокус на облаке, время для мыслей стоит: можно дочитать, ничего не гаснет
let timer: ReturnType<typeof setInterval> | null = null
onMounted(() => {
  brain.start()
  timer = setInterval(() => {
    if (!paused.value) now.value = Date.now() / 1000
  }, 1000)
  window.addEventListener('keydown', onKey)
})
onUnmounted(() => {
  if (timer) clearInterval(timer)
  window.removeEventListener('keydown', onKey)
})

/*
 * Интерактив мозга: клик, тап или Enter по иконке пускает импульс по нейронам и открывает «как думает мозг» поверх облака мыслей (того же размера, панель не растёт):
 * роли с моделью, вызовами и расходом за сегодня. Каждая роль живёт в своей области коры: наведение на строку подсвечивает область на мозге, наведение на нейрон
 * подсвечивает область и роли в списке. Только чтение: никаких команд платформе. Esc и повторный клик закрывают.
 */
const open = ref(new URLSearchParams(location.search).has('brain'))        // ?brain открывает панель сразу (для скриншотов и ссылок)
const icon_ = ref<InstanceType<typeof UiBrainIcon> | null>(null)
const trigger = ref<HTMLButtonElement | null>(null)
const hoverRole = ref<string | null>(null)
const hoverNode = ref(-1)

function toggle(ev: MouseEvent): void {
  icon_.value?.fire(ev)
  open.value = !open.value
  if (!open.value) hoverRole.value = null
}
function onKey(ev: KeyboardEvent): void {
  if (ev.key === 'Escape' && open.value) {
    open.value = false
    hoverRole.value = null
    trigger.value?.focus()
  }
}

const snap = computed(() => brain.snapshot)
const loaded = computed(() => snap.value !== null)
const icon = computed<BrainIconState>(() => (loaded.value ? brain.status : 'idle'))        // до первого снимка не показываем «не работает»
const statusText = computed(() => (loaded.value ? brain.statusText : 'подключение…'))
const thoughts = computed(() => pickThoughts(brain.decisions, now.value))
const active = computed(() => snap.value?.active ?? null)
const spend = computed(() => brain.roles.reduce((s, r) => s + (r.today?.usd ?? 0), 0))
const calls = computed(() => brain.roles.reduce((s, r) => s + (r.today?.calls ?? 0), 0))
const regionMap = computed(() => assignRegions(brain.roles.map((r) => r.role)))
const litRegion = computed(() => (hoverRole.value != null ? regionMap.value[hoverRole.value] ?? -1 : hoverNode.value >= 0 ? regionOfNode(hoverNode.value) : -1))
const lit = computed(() => (litRegion.value >= 0 ? BRAIN_REGIONS[litRegion.value] : []))
// строка роли подсвечивается одна (на какую навели); при наведении на нейрон подсвечиваются все роли его области
const litRoles = computed(() => (hoverRole.value != null ? new Set([hoverRole.value]) : litRegion.value >= 0 ? new Set(rolesOfRegion(regionMap.value, litRegion.value)) : new Set<string>()))
const rows = computed(() => brain.roles.map((r) => ({
  role: r.role, label: roleLabel(r.role), model: r.last?.profile ?? r.primary?.id ?? 'не назначена', last: r.last,
  calls: r.today?.calls ?? 0, usd: r.today?.usd ?? 0, errors: r.today?.errors ?? 0, fb: r.last?.fallback_from ?? null,
})))
if (new URLSearchParams(location.search).get('brain') === 'hl') {        // ?brain=hl: сразу подсветить первую роль (для скриншотов)
  const stop = watch(rows, (r) => { if (r.length) { hoverRole.value = r[0].role; stop() } })
}
const fell = computed(() => brain.roles.filter((r) => r.last?.fallback_from).length)       // отвечал запасной профиль (сервер помечает fallback_from)
</script>

<template>
  <section class="panel" aria-label="Мозг">
    <header class="top">
      <button ref="trigger" type="button" class="brain-btn" :aria-expanded="open" aria-controls="brain-roles" :aria-label="`Мозг: ${statusText}. ${open ? 'Скрыть' : 'Показать'} роли и модели`" title="Как думает мозг"
              @click="toggle">
        <UiBrainIcon ref="icon_" :state="icon" :size="56" interactive :lit="lit" label="" @node="hoverNode = $event" />
      </button>
      <div class="who">
        <div class="model">{{ active?.profile ?? 'модель не отвечала' }}</div>
        <div class="sub">
          <span class="state" :class="loaded ? `st-${brain.status}` : ''">{{ statusText }}</span>
          <template v-if="active"> · {{ active.provider }} · {{ active.role }} · {{ ageText(active.t, now) }}</template>
        </div>
      </div>
    </header>

    <p v-if="snap?.protect_reason" class="alert">{{ snap.protect_reason }}</p>
    <p v-else-if="fell" class="alert soft">отвечает запасная модель у ролей: {{ fell }}</p>

    <!-- область под мысли фиксированной высоты: новые не раздвигают панель, лишние прокручиваются -->
    <div class="stage">
      <div class="sky" @pointerenter="paused = true" @pointerleave="paused = false" @focusin="paused = true" @focusout="paused = false">
        <TransitionGroup name="feed" tag="ul" class="cloud" aria-live="polite">
          <ThoughtBubble v-for="t in thoughts" :key="t.row.seq" :thought="t" :now="now" />
        </TransitionGroup>
        <p v-if="!thoughts.length" class="quiet">{{ !loaded ? '' : brain.status === 'down' ? 'Мозг не отвечает.' : 'Тихо: новых мыслей нет.' }}</p>
      </div>

      <Transition name="roles">
        <div v-if="open" id="brain-roles" class="roles" role="region" aria-label="Как думает мозг: роли и модели">
          <ul v-if="rows.length" class="rlist">
            <li v-for="r in rows" :key="r.role" class="role" :class="{ lit: litRoles.has(r.role) }" tabindex="0"
                @pointerenter="hoverRole = r.role" @pointerleave="hoverRole = null" @focus="hoverRole = r.role" @blur="hoverRole = null">
              <div class="rtop">
                <b class="rname">{{ r.label }}</b>
                <span v-if="r.label !== r.role" class="rid">{{ r.role }}</span>
                <span class="rage">{{ r.last ? ageText(r.last.t, now) : 'не отвечала' }}</span>
              </div>
              <div class="rsub">
                <span class="dot" :class="r.last == null ? 'none' : r.last.ok === false ? 'bad' : 'ok'" aria-hidden="true" />
                {{ r.model }} · {{ r.calls }} выз. · {{ usd(r.usd) }}<template v-if="r.errors"> · ошибок {{ r.errors }}</template>
                <span v-if="r.fb" class="fb">запасная вместо {{ r.fb }}</span>
              </div>
            </li>
          </ul>
          <p v-else class="rnone">Данных по ролям пока нет.</p>
        </div>
      </Transition>
    </div>

    <footer class="foot">
      очередь {{ snap?.queue?.new ?? 0 }} · вызовов сегодня {{ calls }} · {{ usd(spend) }}
    </footer>
  </section>
</template>

<style scoped>
.panel { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: var(--space-4); display: flex; flex-direction: column; gap: var(--space-3); min-width: 0; }
.top { display: flex; align-items: center; gap: var(--space-4); }
.who { min-width: 0; }
.model { font-size: 20px; font-weight: 700; letter-spacing: -0.02em; line-height: 1.15; overflow-wrap: anywhere; }
.sub { font-size: 12px; color: var(--muted); margin-top: 2px; }
.state { font-weight: 600; }
.st-thinking { color: var(--zone-brain); }
.st-protect { color: var(--warn); }
.st-down { color: var(--neg); }
.st-idle { color: var(--pos); }
.alert { margin: 0; padding: var(--space-2) var(--space-3); border: 1px solid var(--warn); border-radius: var(--radius-sm); color: var(--warn); font-size: 13px; }
.alert.soft { border-color: var(--line2); color: var(--muted); }
.stage { position: relative; }
.sky { --thought-h: 76px; position: relative; height: calc(var(--thought-h) * 3 + var(--space-2) * 2); overflow-y: auto; scrollbar-gutter: stable; }
.brain-btn { flex: none; padding: 4px; margin: -4px; background: transparent; border: 1px solid transparent; border-radius: 50%; color: inherit; cursor: pointer; display: grid; place-items: center; transition: border-color var(--dur-fast) ease; }
.brain-btn[aria-expanded='true'] { border-color: var(--line2); }
.brain-btn:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
@media (hover: hover) and (pointer: fine) {
  .brain-btn:hover { border-color: var(--line2); }
}

/* роли поверх облака мыслей: тот же размер, панель не растёт; редкое открытие, поэтому короткий выезд, закрытие быстрее */
.roles { position: absolute; inset: 0; overflow-y: auto; scrollbar-gutter: stable; background: var(--panel); border: 1px solid var(--line2); border-radius: var(--radius-sm); padding: var(--space-2); }
.rlist { list-style: none; margin: 0; padding: 0; display: grid; gap: 2px; }
.role { padding: var(--space-2) var(--space-3); border-radius: var(--radius-sm); outline: none; cursor: default; transition: background-color var(--dur-fast) ease; }
.role.lit { background: var(--accent-bg); }
.role:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.rtop { display: flex; align-items: baseline; gap: var(--space-2); min-width: 0; }
.rname { font-size: 13px; }
.rid { font-size: 11px; color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rage { margin-left: auto; font-size: 11px; color: var(--muted); white-space: nowrap; font-variant-numeric: tabular-nums; }
.rsub { margin-top: 2px; font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.dot { display: inline-block; width: 6px; height: 6px; margin-right: 6px; border-radius: 50%; vertical-align: middle; background: var(--line2); }
.dot.ok { background: var(--pos); }
.dot.bad { background: var(--neg); }
.fb { margin-left: var(--space-2); color: var(--warn); }
.rnone { margin: 0; height: 100%; display: grid; place-items: center; color: var(--muted); font-size: 13px; }
.roles-enter-from, .roles-leave-to { opacity: 0; }
.roles-enter-from { transform: translateY(6px); }
.roles-enter-active { transition: opacity var(--dur-enter) var(--ease-out), transform var(--dur-enter) var(--ease-out); }
.roles-leave-active { transition: opacity 120ms var(--ease-out); }
@media (prefers-reduced-motion: reduce) {
  .roles-enter-from { transform: none; }
  .role { transition: none; }
}
.cloud { list-style: none; margin: 0; padding: 0; display: grid; gap: var(--space-2); position: relative; }
.quiet { position: absolute; inset: 0; margin: 0; display: grid; place-items: center; color: var(--muted); font-size: 13px; }
.foot { margin-top: auto; padding-top: var(--space-2); border-top: 1px solid var(--line); font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
</style>
