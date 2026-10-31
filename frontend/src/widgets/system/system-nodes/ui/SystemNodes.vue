<script setup lang="ts">
import { computed } from 'vue'
import { nodeLine, sortNodes, useSystemStore } from '@/entities/system'
import { useNow } from '@/shared/lib'

/** Узлы платформы плитками: исполнитель, связь, сверка, шина событий, сканер, фид, контекст рынка, сессия. Проблемные идут первыми и выделены цветом; у каждого одна строка: что он делает и как давно обновлялся. */
const sys = useSystemStore()
const now = useNow()
const nodes = computed(() => sortNodes(sys.snapshot?.nodes ?? []))
const WORD: Record<string, string> = { ok: 'в норме', warn: 'внимание', bad: 'проблема', off: 'не активно', info: 'сведения' }
</script>

<template>
  <section class="nodes" aria-label="Узлы платформы">
    <h3 class="cap">Узлы платформы</h3>
    <ul v-if="nodes.length" class="grid">
      <li v-for="n in nodes" :key="n.id" class="tile" :class="`lv-${n.level}`">
        <span class="head"><i class="dot" aria-hidden="true" /><b>{{ n.label }}</b><span class="word">{{ WORD[n.level] ?? n.level }}</span></span>
        <span class="line">{{ nodeLine(n, now) }}</span>
      </li>
    </ul>
    <div v-else class="skel" aria-busy="true" />
  </section>
</template>

<style scoped>
.nodes { display: grid; gap: var(--space-3); }
.cap { margin: 0; font-size: 12px; font-weight: 600; color: var(--muted); }
.grid { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: var(--space-3); }
.tile { display: grid; gap: 3px; padding: var(--space-3) var(--space-4); background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius-sm); min-width: 0; }
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
.skel { height: 120px; border-radius: var(--radius-sm); background: var(--panel); }
</style>
