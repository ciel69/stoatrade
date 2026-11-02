<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useDayStore } from '@/entities/day'
import { UiLogo, UiWordmark } from '@/shared/ui'
import { NAV_GROUPS, isActive } from '@/shared/config'

/**
 * Боковая колонка разделов. Развёрнутая: значок и название, группы с подписями; свёрнутая: только значки (название в подсказке и для скринридера), состояние помнится в браузере.
 * Узкий экран: колонка скрыта, кнопка в левом верхнем углу выдвигает её поверх страницы, фон и Esc закрывают, переход по разделу закрывает сам.
 * Движение (animate): раскрытие и сворачивание редкое действие, ширина меняется за 200 мс ease-out, подписи проявляются opacity; на узком экране колонка выезжает transform.
 */
/* Локап E (docs/brand/stoa/concept-2b-lockup.svg): знак на две строки, справа «Stoa» контурами и подпись «agentic trading» живым текстом 11 px.
 * Всё в единицах слова: WORD px на единицу, краска S от CAP, база подписи BASE. Краска знака (25.5 из 32 ед.) встаёт ровно от верха S до базы подписи,
 * отсюда размер коробки знака MARK ≈ 38.6 px; в CSS то же число в --mark. */
const WORD = 0.86
const CAP = 4.5
const BASE = 40.3
const MARK = (32 * (BASE - CAP) / 25.5) * WORD
const TAGLINE = 'agentic trading'
const KEY = 'tb.nav.collapsed'
const read = (): boolean => {
  try { return localStorage.getItem(KEY) === '1' } catch { return false }
}
const collapsed = ref(new URLSearchParams(location.search).get('nav') === 'min' || read())        // ?nav=min: свёрнутая колонка сразу (для скриншотов)
const drawer = ref(false)               // узкий экран: колонка выдвинута
const route = useRoute()
const closeBtn = ref<HTMLButtonElement | null>(null)
// счётчик «ждёт вашего ответа» (вопросы о переносе, возобновление дня, итог дня): виден на любой странице, не только на «Дне»
const day = useDayStore()
const count = (key: string | undefined): number => (key === 'day' ? day.needs.count : 0)
const countWord = (n: number): string => `, ждёт вашего ответа: ${n}`

function toggle(): void {
  collapsed.value = !collapsed.value
  try { localStorage.setItem(KEY, collapsed.value ? '1' : '0') } catch { /* память браузера недоступна: состояние на эту вкладку */ }
}
watch(() => route.fullPath, () => { drawer.value = false })
watch(drawer, (v) => { if (v) queueMicrotask(() => closeBtn.value?.focus()) })
function onKey(ev: KeyboardEvent): void {
  if (ev.key === 'Escape' && drawer.value) drawer.value = false
}
onMounted(() => {
  window.addEventListener('keydown', onKey)
  day.start()
})
onUnmounted(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <button type="button" class="burger" aria-label="Открыть разделы" :aria-expanded="drawer" aria-controls="app-nav" @click="drawer = true">
    <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16" /></svg>
  </button>
  <div v-if="drawer" class="backdrop" @click="drawer = false" />

  <aside id="app-nav" class="nav" :class="{ collapsed, drawer }" aria-label="Разделы">
    <div class="brand" :style="{ '--mark': `${MARK}px` }">
      <UiLogo :size="MARK" :stroke="2.7" />
      <UiWordmark class="name" :unit="WORD" :stroke="3" :tagline="TAGLINE" :tagline-px="11" :tagline-base="BASE" />
      <button v-if="drawer" ref="closeBtn" type="button" class="icon-btn close" aria-label="Закрыть разделы" @click="drawer = false">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>
      </button>
    </div>

    <!-- действие над разделами (старт торговли): приходит из app, в свёрнутой колонке получает collapsed и рисуется значком -->
    <div class="action"><slot name="action" :collapsed="collapsed && !drawer" /></div>

    <nav class="groups">
      <div v-for="g in NAV_GROUPS" :key="g.id" class="group">
        <p v-if="NAV_GROUPS.length > 1" class="cap">{{ g.label }}</p>
        <RouterLink v-for="it in g.items" :key="it.to" :to="it.to" class="item" :class="{ active: isActive(it, route.path) }" :aria-current="isActive(it, route.path) ? 'page' : undefined" :style="{ '--zc': `var(--zone-${it.zone})` }" :title="collapsed ? it.label : undefined" :aria-label="collapsed ? it.label : undefined">
          <span class="tile"><svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path :d="it.icon" /></svg></span>
          <span class="label">{{ it.label }}</span>
          <Transition name="badge">
            <span v-if="count(it.badge)" class="badge">{{ count(it.badge) }}<span class="sr">{{ countWord(count(it.badge)) }}</span></span>
          </Transition>
        </RouterLink>
      </div>
    </nav>

    <button type="button" class="icon-btn fold" :aria-label="collapsed ? 'Развернуть колонку' : 'Свернуть колонку'" :aria-pressed="collapsed" @click="toggle">
      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" class="chev"><path d="M15 6l-6 6 6 6" /></svg>
    </button>
  </aside>
</template>

<style scoped>
/* Плавающая карточка со скруглением и значками-плитками цвета раздела, как в боковой панели старого дашборда. */
.nav {
  position: sticky; top: 12px; align-self: start; height: calc(100vh - 24px); width: 220px; margin: 12px 0 12px 12px; box-sizing: border-box; display: flex; flex-direction: column; gap: 24px;
  padding: 17px 12px 12px; background: var(--panel); border: 1px solid var(--line); border-radius: 20px; box-shadow: var(--lift); overflow: hidden;
  transition: width var(--dur-enter) var(--ease-out);
}
.nav.collapsed { width: 80px; }        /* пункт 48 (плитка 32 + поля по 8) в содержимом 54 (80 - рамка 2 - поля 24): по 3 px запаса с каждой стороны, чтобы рамка и фокус не резались */
.nav.collapsed .action, .nav.collapsed .group { justify-items: center; }
.nav.collapsed .item { width: 48px; box-sizing: border-box; }
/* Локап E. В коробке знака 32×32 краска отступает от боков на 5.25 ед. (--inset) и от верха на 3.25 ед. (--inset-top), поэтому поля считаются от краски:
 * левый край знака на 0.5 px левее плитки пункта (x = 8, свес круглой дуги), между краской знака и S 7 px. Вертикаль: верх слова (его viewBox начинается
 * с краски S) опущен на --inset-top, так верх S совпадает с верхом знака, а база подписи (BASE) — с низом знака. Верх: поле 17 px, до краски ~21 px, как слева. */
.brand { --mark: 38.64px; --inset: calc(var(--mark) * 5.25 / 32); --inset-top: calc(var(--mark) * 3.25 / 32); --pad: calc(8px - var(--inset) - 0.5px);
  display: flex; align-items: flex-start; gap: calc(7px - var(--inset)); min-height: var(--mark); padding: 0 0 0 var(--pad); }
.brand .name { margin-top: var(--inset-top); }
.brand .close { align-self: center; }
.brand :deep(svg) { flex: none; }
.brand .logo { transition: transform var(--dur-enter) var(--ease-out); }
/* свёрнутая: знак едет в центр колонки (содержимое 54 = 80 - рамка 2 - поля 24) вместе с изменением ширины, transform без перекладки */
.nav.collapsed .brand .logo { transform: translateX(calc((54px - var(--mark)) / 2 - var(--pad))); }
.name { color: var(--text); white-space: nowrap; transition: opacity var(--dur-enter) var(--ease-out); }
.action { display: grid; }
.action:empty { display: none; }
.groups { display: grid; gap: var(--space-4); flex: 1 1 auto; align-content: start; overflow-y: auto; overflow-x: hidden; padding: 3px; margin: -3px; }        /* поле под обводку фокуса: иначе её режет overflow */
.group { display: grid; gap: 4px; }
.cap { margin: 0 0 var(--space-1); padding: 0 var(--space-3); font-size: 11px; color: var(--muted); white-space: nowrap; height: 14px; transition: opacity var(--dur-enter) var(--ease-out); }
.item { --zc: var(--muted); position: relative; display: flex; align-items: center; gap: 12px; padding: 8px; border-radius: 12px; color: var(--muted); font-size: 14px; font-weight: 600; text-decoration: none; white-space: nowrap; transition: color var(--dur-fast) ease, background-color var(--dur-fast) ease, box-shadow var(--dur-fast) ease; }
.tile { flex: none; display: grid; place-items: center; width: 32px; height: 32px; border-radius: 9px; color: var(--zc); background: color-mix(in srgb, var(--zc) 16%, transparent); transition: background-color var(--dur-enter) var(--ease-out), color var(--dur-enter) var(--ease-out); }
.label { transition: opacity var(--dur-enter) var(--ease-out); }
.badge { margin-left: auto; min-width: 18px; height: 18px; padding: 0 5px; border-radius: 99px; background: var(--warn); color: var(--bg); font-size: 11px; font-weight: 700; line-height: 18px; text-align: center; font-variant-numeric: tabular-nums; }
.nav.collapsed .badge { position: absolute; top: 0; right: 0; margin: 0; }
.sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
.badge-enter-active { transition: opacity var(--dur-fast) var(--ease-out), transform var(--dur-fast) var(--ease-out); }
.badge-leave-active { transition: opacity 120ms var(--ease-out); }
.badge-enter-from { opacity: 0; transform: scale(0.8); }
.badge-leave-to { opacity: 0; }
.item.active { color: var(--text); background: color-mix(in srgb, var(--zc) 13%, var(--panel)); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--zc) 30%, transparent); }
.item.active .tile { background: var(--zc); color: var(--bg); }
.item:focus-visible, .icon-btn:focus-visible, .burger:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
@media (hover: hover) and (pointer: fine) {
  .item:not(.active):hover { color: var(--text); background: var(--panel2); }
  .icon-btn:hover { color: var(--text); }
}
/* свёрнутая: подписи не только невидимы, но и не занимают места, иначе пункт шире колонки и его рамка обрезается */
.nav.collapsed .name, .nav.collapsed .label { opacity: 0; width: 0; overflow: hidden; }
.nav.collapsed .brand, .nav.collapsed .item { gap: 0; }
.nav.collapsed .cap { opacity: 0; }
.icon-btn { display: grid; place-items: center; width: 32px; height: 32px; padding: 0; background: transparent; border: 0; border-radius: var(--radius-sm); color: var(--muted); cursor: pointer; transition: color var(--dur-fast) ease; }
.fold { align-self: flex-end; }
.nav.collapsed .fold { align-self: center; }
.chev { transition: transform var(--dur-enter) var(--ease-out); }
.nav.collapsed .chev { transform: rotate(180deg); }
.close { margin-left: auto; }

.burger { display: none; }
.backdrop { display: none; }

/* узкий экран: колонка прячется за левый край и выезжает поверх страницы */
@media (max-width: 720px) {
  .burger { position: fixed; z-index: 30; top: 10px; left: 10px; display: grid; place-items: center; width: 38px; height: 38px; padding: 0; background: var(--panel); color: var(--text); border: 1px solid var(--line2); border-radius: var(--radius-sm); cursor: pointer; }
  .nav, .nav.collapsed { position: fixed; z-index: 41; top: 0; left: 0; bottom: 0; width: 248px; max-width: 86vw; height: auto; margin: 0; border-radius: 0 20px 20px 0; transform: translateX(-102%); transition: transform var(--dur-enter) var(--ease-out); box-shadow: none; }
  .nav.drawer { transform: none; box-shadow: 0 0 40px rgba(0, 0, 0, 0.45); }
  .nav.collapsed .name, .nav.collapsed .label, .nav.collapsed .cap { opacity: 1; }
  .nav.collapsed .name, .nav.collapsed .label { width: auto; overflow: visible; }        /* выдвижная колонка всегда развёрнута, даже если на широком экране её свернули */
  .nav.collapsed .brand { gap: calc(7px - var(--inset)); }
  .nav.collapsed .item { gap: 12px; width: auto; }
  .nav.collapsed .brand .logo { transform: none; }
  .fold { display: none; }
  .backdrop { display: block; position: fixed; z-index: 40; inset: 0; background: rgba(0, 0, 0, 0.5); }
}
@media (prefers-reduced-motion: reduce) {
  .nav, .name, .cap, .label, .chev, .tile, .brand .logo { transition: none; }
  .badge-enter-from { transform: none; }
}
</style>
