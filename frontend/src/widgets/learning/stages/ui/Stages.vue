<script setup lang="ts">
import { computed } from 'vue'
import type { LearningBlock } from '@/entities/learning'
import { stageBlocks } from '../lib/stages'

/** Блоки следующих этапов (уроки, версии мозга до/после): пока не считаются, честно говорят когда появятся. Без данных и без движения. */
const props = defineProps<{ blocks: LearningBlock[] }>()
const items = computed(() => stageBlocks(props.blocks))
</script>

<template>
  <section v-if="items.length" class="st" aria-label="Блоки следующих этапов">
    <article v-for="s in items" :key="s.id" class="item">
      <h2 class="title">{{ s.title }}</h2>
      <p class="when">{{ s.stage }}</p>
      <p class="about">{{ s.about }}</p>
    </article>
  </section>
</template>

<style scoped>
.st { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 260px), 1fr)); gap: var(--space-3); min-width: 0; }
.item { background: var(--panel); border: 1px dashed var(--line2); border-radius: var(--radius); padding: var(--space-4); min-width: 0; }
.title { margin: 0; font-size: 14px; font-weight: 600; color: var(--muted); }
.when { margin: var(--space-1) 0 var(--space-2); font-size: 13px; font-weight: 600; }
.about { margin: 0; font-size: 12px; line-height: 1.4; color: var(--muted); }
</style>
