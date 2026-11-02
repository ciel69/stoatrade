<script setup lang="ts">
import type { Mark } from '@/entities/brief'
import { UiChip } from '@/shared/ui'

/** Бейдж сравнения с прошлым досье у заголовка блока или строки: «изменилось» или «новое». Нет пометки: ничего не рисуется. */
defineProps<{ mark?: Mark | 'fresh' }>()
</script>

<template>
  <UiChip v-if="mark" tone="accent" class="mk" :title="mark === 'changed' ? 'Отличается от прошлого досье' : 'Этого не было в прошлом досье'">{{ mark === 'changed' ? 'изменилось' : 'новое' }}</UiChip>
</template>
