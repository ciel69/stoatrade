<script setup lang="ts">
withDefaults(defineProps<{ title?: string; sub?: string; as?: string }>(), { as: 'section' })
</script>

<template>
  <component :is="as" class="card">
    <header v-if="title || $slots.head" class="head">
      <h2 v-if="title" class="title">{{ title }}<span v-if="sub" class="sub">{{ sub }}</span></h2>
      <slot name="head" />
    </header>
    <slot />
  </component>
</template>

<style scoped>
.card {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: var(--space-4);
}
.head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: var(--space-3);
}
.title {
  margin: 0;
  font-size: 15px;
  font-weight: 700;
  letter-spacing: -0.01em;
}
.sub {
  margin-left: var(--space-2);
  font-weight: 400;
  font-size: 12px;
  color: var(--muted);
}
</style>
