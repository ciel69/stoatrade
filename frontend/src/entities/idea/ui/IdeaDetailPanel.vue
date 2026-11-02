<script setup lang="ts">
import { UiChip, UiDetail } from '@/shared/ui'
import { STATUS_RU } from '../lib/idea'
import type { IdeaItem } from '../model/types'
import IdeaDetail from './IdeaDetail.vue'

/** Содержимое боковой панели идеи: монета, сторона, статус и подробности (тезис, цены, что было бы, план и сделка, пометки). */
defineProps<{ idea: IdeaItem }>()
defineEmits<{ close: [] }>()
</script>

<template>
  <UiDetail :label="`Идея ${idea.sym}`" @close="$emit('close')">
    <template #title>
      <b class="sym">{{ idea.sym }}</b>
      <UiChip>{{ idea.side === 'long' ? 'лонг' : idea.side === 'short' ? 'шорт' : '?' }}</UiChip>
      <UiChip :tone="idea.status === 'taken' || idea.status === 'planned' ? 'ok' : 'neutral'">{{ STATUS_RU[idea.status] ?? idea.status }}</UiChip>
    </template>
    <IdeaDetail :idea="idea" />
  </UiDetail>
</template>

<style scoped>
.sym { font-size: 18px; letter-spacing: -0.01em; }
</style>
