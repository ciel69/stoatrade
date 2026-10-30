<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { UiBrainIcon } from '@/shared/ui'
import { NAV_GROUPS } from '../model/nav'

/**
 * Боковая колонка разделов. Развёрнутая: значок и название, группы с подписями; свёрнутая: только значки (название в подсказке и для скринридера), состояние помнится в браузере.
 * Узкий экран: колонка скрыта, кнопка в левом верхнем углу выдвигает её поверх страницы, фон и Esc закрывают, переход по разделу закрывает сам.
 * Движение (animate): раскрытие и сворачивание редкое действие, ширина меняется за 200 мс ease-out, подписи проявляются opacity; на узком экране колонка выезжает transform.
 */
const KEY = 'tb.nav.collapsed'
const read = (): boolean => {
  try { return localStorage.getItem(KEY) === '1' } catch { return false }
}
const collapsed = ref(read())
const drawer = ref(false)               // узкий экран: колонка выдвинута
const route = useRoute()
const closeBtn = ref<HTMLButtonElement | null>(null)

function toggle(): void {
  collapsed.value = !collapsed.value
  try { localStorage.setItem(KEY, collapsed.value ? '1' : '0') } catch { /* память браузера недоступна: состояние на эту вкладку */ }
}
watch(() => route.fullPath, () => { drawer.value = false })
watch(drawer, (v) => { if (v) queueMicrotask(() => closeBtn.value?.focus()) })
function onKey(ev: KeyboardEvent): void {
  if (ev.key === 'Escape' && drawer.value) drawer.value = false
}
onMounted(() => window.addEventListener('keydown', onKey))
onUnmounted(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <button type="button" class="burger" aria-label="Открыть разделы" :aria-expanded="drawer" aria-controls="app-nav" @click="drawer = true">
    <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16" /></svg>
  </button>
  <div v-if="drawer" class="backdrop" @click="drawer = false" />

  <aside id="app-nav" class="nav" :class="{ collapsed, drawer }" aria-label="Разделы">
    <div class="brand">
      <UiBrainIcon :size="30" label="" />
      <span class="name">open-trader</span>
      <button v-if="drawer" ref="closeBtn" type="button" class="icon-btn close" aria-label="Закрыть разделы" @click="drawer = false">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>
      </button>
    </div>

    <nav class="groups">
      <div v-for="g in NAV_GROUPS" :key="g.id" class="group">
        <p class="cap">{{ g.label }}</p>
        <RouterLink v-for="it in g.items" :key="it.to" :to="it.to" class="item" :title="collapsed ? it.label : undefined" :aria-label="collapsed ? it.label : undefined">
          <svg class="ico" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path :d="it.icon" /></svg>
          <span class="label">{{ it.label }}</span>
        </RouterLink>
      </div>
    </nav>

    <button type="button" class="icon-btn fold" :aria-label="collapsed ? 'Развернуть колонку' : 'Свернуть колонку'" :aria-pressed="collapsed" @click="toggle">
      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" class="chev"><path d="M15 6l-6 6 6 6" /></svg>
    </button>
  </aside>
</template>

<style scoped>
.nav {
  position: sticky; top: 0; align-self: start; height: 100vh; width: 208px; box-sizing: border-box; display: flex; flex-direction: column; gap: var(--space-4);
  padding: var(--space-4) var(--space-3); background: var(--panel); border-right: 1px solid var(--line); overflow: hidden;
  transition: width var(--dur-enter) var(--ease-out);
}
.nav.collapsed { width: 64px; }
.brand { display: flex; align-items: center; gap: var(--space-3); min-height: 34px; padding: 0 var(--space-1); }
.brand :deep(svg) { flex: none; }
.name { font-size: 15px; font-weight: 700; letter-spacing: -0.02em; white-space: nowrap; transition: opacity var(--dur-enter) var(--ease-out); }
.groups { display: grid; gap: var(--space-4); flex: 1 1 auto; align-content: start; overflow-y: auto; overflow-x: hidden; }
.group { display: grid; gap: 2px; }
.cap { margin: 0 0 var(--space-1); padding: 0 var(--space-3); font-size: 11px; color: var(--muted); white-space: nowrap; height: 14px; transition: opacity var(--dur-enter) var(--ease-out); }
.item { display: flex; align-items: center; gap: var(--space-3); height: 36px; padding: 0 10px; border-radius: var(--radius-sm); color: var(--muted); font-size: 13px; text-decoration: none; white-space: nowrap; transition: color var(--dur-fast) ease, background-color var(--dur-fast) ease; }
.ico { flex: none; }
.label { transition: opacity var(--dur-enter) var(--ease-out); }
.item.router-link-exact-active { background: var(--accent-bg); color: var(--accent); }
.item:focus-visible, .icon-btn:focus-visible, .burger:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
@media (hover: hover) and (pointer: fine) {
  .item:not(.router-link-exact-active):hover { color: var(--text); background: var(--panel2); }
  .icon-btn:hover { color: var(--text); }
}
.nav.collapsed .name, .nav.collapsed .label { opacity: 0; }
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
  .nav, .nav.collapsed { position: fixed; z-index: 41; top: 0; left: 0; bottom: 0; width: 240px; max-width: 86vw; height: auto; transform: translateX(-102%); transition: transform var(--dur-enter) var(--ease-out); box-shadow: none; }
  .nav.drawer { transform: none; box-shadow: 0 0 40px rgba(0, 0, 0, 0.45); }
  .nav.collapsed .name, .nav.collapsed .label, .nav.collapsed .cap { opacity: 1; }
  .fold { display: none; }
  .backdrop { display: block; position: fixed; z-index: 40; inset: 0; background: rgba(0, 0, 0, 0.5); }
}
@media (prefers-reduced-motion: reduce) {
  .nav, .name, .cap, .label, .chev { transition: none; }
}
</style>
