<script setup lang="ts">
import { computed } from 'vue'
import { sortNodes, useSystemStore } from '@/entities/system'
import { humanize, nodeLabel, nodeValue, useNow } from '@/shared/lib'

/**
 * Узлы платформы плитками: исполнитель, связь с биржей, сверка, передача событий, сканер, поток цен, настроение рынка, сессия. Проблемные идут первыми.
 * У каждой плитки простое название, одна строка «как дела» и, если что-то не так, что это значит и что делать. Служебные тексты сервера переводит общий словарь.
 */
const sys = useSystemStore()
const now = useNow()
const WORD: Record<string, string> = { ok: 'в порядке', info: 'в порядке', warn: 'стоит проверить', bad: 'нужно действие', off: 'не используется' }
const tiles = computed(() => sortNodes(sys.snapshot?.nodes ?? []).map((n) => {
  const h = n.level === 'warn' || n.level === 'bad' || n.level === 'off' ? humanize({ kind: 'node', node: n }) : null
  return { id: n.id, level: n.level, name: nodeLabel(n.id, n.label), word: WORD[n.level] ?? '', line: nodeValue(n, now.value), help: h && h.tone !== 'ok' ? [h.title, h.todo].filter(Boolean).join('. ').replace(/([^.])$/, '$1.') : '' }
}))
</script>

<template>
  <section class="nodes" aria-label="Узлы платформы">
    <h3 class="cap">Из чего состоит платформа</h3>
    <ul v-if="tiles.length" class="grid">
      <li v-for="n in tiles" :key="n.id" class="tile" :class="`lv-${n.level}`">
        <span class="head"><i class="dot" aria-hidden="true" /><b>{{ n.name }}</b><span class="word">{{ n.word }}</span></span>
        <span class="line">{{ n.line }}</span>
        <span v-if="n.help" class="help">{{ n.help }}</span>
      </li>
    </ul>
    <div v-else class="skel" aria-busy="true" />
  </section>
</template>

<style scoped>
.nodes { display: grid; gap: var(--space-3); }
.cap { margin: 0; font-size: 12px; font-weight: 600; color: var(--muted); }
.grid { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: var(--space-3); }
.tile { display: grid; gap: 3px; align-content: start; padding: var(--space-3) var(--space-4); background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius-sm); min-width: 0; }
.tile.lv-warn { border-color: color-mix(in srgb, var(--warn) 45%, var(--line)); }
.tile.lv-bad { border-color: color-mix(in srgb, var(--neg) 55%, var(--line)); background: color-mix(in srgb, var(--neg) 6%, var(--panel)); }
.head { display: flex; align-items: center; gap: var(--space-2); font-size: 13px; }
.dot { flex: none; width: 8px; height: 8px; border-radius: 50%; background: var(--pos); }
.lv-warn .dot { background: var(--warn); }
.lv-bad .dot { background: var(--neg); }
.lv-off .dot { background: var(--line2); }
.word { margin-left: auto; font-size: 11px; color: var(--muted); }
.lv-warn .word { color: var(--warn); }
.lv-bad .word { color: var(--neg); }
.line { font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.help { font-size: 12px; line-height: 1.5; color: var(--text); text-wrap: pretty; }
.skel { height: 120px; border-radius: var(--radius-sm); background: var(--panel); }
</style>
