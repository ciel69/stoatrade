<script setup lang="ts">
import { onErrorCaptured, ref } from 'vue'
import MemoryBlockState from './MemoryBlockState.vue'

/** Предохранитель блока «Памяти»: сбой отрисовки одного блока показывает причину на его месте и не доходит до остальной страницы и дашборда. */
defineProps<{ what: string }>()
const failed = ref(false)
onErrorCaptured((e) => {
  failed.value = true
  console.warn('Блок вкладки «Память» не отрисован:', e)
  return false
})
</script>

<template>
  <section v-if="failed" class="msafe" :aria-label="`Блок ${what}`">
    <MemoryBlockState state="error" :what="what" />
  </section>
  <slot v-else />
</template>

<style scoped>
.msafe { padding: var(--space-4); background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); }
</style>
