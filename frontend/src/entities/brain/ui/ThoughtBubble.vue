<script setup lang="ts">
import { computed } from 'vue'
import { ageText } from '@/shared/lib'
import { FADE_S } from '../lib/thoughts'
import type { Thought } from '../lib/thoughts'

const props = defineProps<{ thought: Thought; now: number }>()
const row = computed(() => props.thought.row)
const kind = computed(() => (row.value.what === 'brain_why' ? 'объяснил' : row.value.decision ? decisionText(row.value.decision) : row.value.ok === false ? 'отказ' : 'разобрал'))

// Растворение целиком на CSS: одна анимация на всю жизнь мысли, начатая с её текущего возраста (отрицательная задержка). Фиксируем один раз при создании,
// иначе каждое обновление возраста перезапускало бы анимацию.
const lifeStyle = {
  '--life': `${FADE_S}s`,
  '--age': `${-Math.max(0, props.now - props.thought.row.t)}s`,
}

function decisionText(d: string): string {
  const t: Record<string, string> = { move_stop: 'подтянул стоп', close: 'закрыл', partial_close: 'закрыл часть', cancel: 'отменил вход', amend: 'поправил план', escalate: 'позвал владельца', hold: 'держит' }
  return t[d] ?? d
}
</script>

<template>
  <li class="thought" tabindex="0">
    <div class="fade" :style="lifeStyle">
      <div class="line">
        <b v-if="row.sym" class="sym">{{ row.sym }}</b>
        <span class="kind">{{ kind }}</span>
        <span class="age">{{ ageText(row.t, now) }}</span>
      </div>
      <p class="text">{{ row.text }}</p>
    </div>
  </li>
</template>

<style scoped>
.thought {
  height: var(--thought-h);
  box-sizing: border-box;
  padding: var(--space-2) var(--space-3);
  background: var(--panel2);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  overflow: hidden;
}
/* Растворение: прозрачность и лёгкое размытие плавно растут за всё время жизни, движение постоянное, поэтому linear. Пауза при наведении на облако (см. BrainPanel). */
.fade {
  animation: thought-dissolve var(--life) linear var(--age) both;
  animation-play-state: var(--cloud-play, running);
}
.thought:focus-visible .fade { animation-play-state: paused; opacity: 1; filter: none; }
.line { display: flex; align-items: baseline; gap: var(--space-2); font-size: 12px; color: var(--muted); }
.sym { color: var(--text); font-size: 13px; }
.kind { color: var(--zone-brain); }
.age { margin-left: auto; font-variant-numeric: tabular-nums; }
.text { margin: 2px 0 0; font-size: 13px; line-height: 1.4; overflow-wrap: anywhere; display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }

@keyframes thought-dissolve {
  0%, 20% { opacity: 1; filter: blur(0); }
  100% { opacity: 0.12; filter: blur(1.2px); }
}
@media (prefers-reduced-motion: reduce) {
  @keyframes thought-dissolve {
    0%, 20% { opacity: 1; }
    100% { opacity: 0.35; }
  }
}
</style>
