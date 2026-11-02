<script setup lang="ts">
import { computed } from 'vue'
import { looksPrivate } from '@/entities/role'
import { reflectText } from '@/shared/lib'
import type { ProviderNet } from '../model/useProviderNet'

/**
 * Адрес провайдера: текущий хост и смена адреса (base URL) с флажком «Адрес во внутренней сети». Пустое поле значит «адрес не менять» (в данных есть только хост, не полный адрес).
 * Смена адреса у провайдера с ключом отправляет ключ на новый адрес: об этом сказано до отправки, а платформа показывает отдельное предупреждение в общем диалоге.
 */
const props = defineProps<{ net: ProviderNet; hasKey: boolean }>()
const n = props.net
const hint = computed(() => !n.addrAllow.value && !n.addrUrlError.value && looksPrivate(n.addr.value.baseUrl))
</script>

<template>
  <section class="pe-sec" aria-labelledby="pad-h">
    <div class="pe-hrow"><h4 id="pad-h">Адрес</h4></div>
    <p class="pe-meta">Сейчас: {{ n.row.value?.base_host ?? 'не указан' }}{{ n.row.value?.allow_private ? '. Внутренняя сеть разрешена.' : '.' }}</p>
    <form class="fld" novalidate @submit.prevent="n.submitAddress()">
      <label for="pad-url">Новый адрес REST (base URL)</label>
      <input id="pad-url" class="pe-input" type="text" inputmode="url" autocomplete="off" spellcheck="false" autocapitalize="off" placeholder="оставьте пустым, чтобы не менять" :value="n.addr.value.baseUrl" :disabled="n.busy.value" :aria-invalid="!!n.addrUrlError.value" aria-describedby="pad-url-h" @input="n.setBaseUrl(($event.target as HTMLInputElement).value)">
      <p id="pad-url-h" class="pe-meta" :class="{ bad: !!n.addrUrlError.value, warn: !n.addrUrlError.value && hasKey && !!n.addr.value.baseUrl.trim() }">{{ n.addrUrlError.value ?? (hasKey && n.addr.value.baseUrl.trim() ? 'Ключ провайдера после смены будет уходить на новый адрес. Убедитесь, что это нужный сервис.' : 'Только https. Ключ уходит на этот адрес.') }}</p>
      <div class="chk">
        <input id="pad-priv" type="checkbox" :checked="n.addrAllow.value" :disabled="n.busy.value" @change="n.setAllowPrivate(($event.target as HTMLInputElement).checked)">
        <label for="pad-priv">Адрес во внутренней сети</label>
      </div>
      <Transition name="pe">
        <p v-if="hint" class="pe-meta warn" role="status">Адрес похож на внутренний. Без флажка платформа его не примет.</p>
      </Transition>
      <p v-if="n.addrFail.value" class="pe-meta bad" role="alert">{{ n.addrFail.value }}</p>
      <div class="pe-acts">
        <button v-if="n.patch.value || n.addr.value.baseUrl || n.addr.value.touched" type="button" class="pe-ghost" :disabled="n.busy.value" @click="n.resetAddr()">Сбросить</button>
        <button type="submit" class="pe-cta" :disabled="n.busy.value || !n.patch.value">{{ n.running.value === 'address' ? 'Сохраняю…' : 'Сохранить адрес' }}</button>
      </div>
      <p class="pe-meta" :class="{ warn: !!n.addrNote.value }" role="status" aria-live="polite">{{ n.addrNote.value || reflectText(n.addrWait.state.value) }}</p>
    </form>
  </section>
</template>

<style scoped>
.fld { display: grid; gap: 6px; min-width: 0; }
label { font-size: 12px; color: var(--text); }
.chk { display: flex; align-items: center; gap: var(--space-2); min-height: 36px; }
.chk input { flex: none; width: 18px; height: 18px; margin: 0; accent-color: var(--accent); }
.chk input:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.chk label { cursor: pointer; }
p[role='status']:empty { display: none; }
</style>
