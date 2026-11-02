<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { roleLabel } from '@/entities/role'
import { panelSubtitle, pctText, toksShare, toksTip, usageKey, useUsageStore, withShares, type UsageBy, type UsagePeriod } from '@/entities/usage'
import { UiDetail } from '@/shared/ui'
import { roleAbout } from '@/shared/lib'

/**
 * Боковая панель «Расход группы»: что стоит за строкой блока «Сколько стоит». Сверху крупно «всего токенов» и среднее на вызов, ниже сегментная полоса состава токенов
 * (кэш входа, вход мимо кэша, выход-ответ, рассуждение) с легендой и долями, затем сетка скорости и качества с короткой справкой (проблемные значения подсвечены порогом строки),
 * цена вызова и за 1 млн токенов. Данные берёт из стора по ключу «период:разрез» из адреса; если их нет, запрашивает и показывает загрузку. Только чтение.
 * Движение: сегменты раскрываются один раз при открытии (30 мс на сегмент, до 240 мс), при обновлении данных числа меняются на месте, без перерисовки.
 */
const props = defineProps<{ id: string; days: UsagePeriod; by: UsageBy }>()
defineEmits<{ close: [] }>()
const store = useUsageStore()
const key = computed(() => usageKey(props.days, props.by))
const reply = computed(() => store.replies[key.value] ?? null)
const loading = computed(() => !reply.value && (store.pending[key.value] ?? true))
const groups = computed(() => withShares(reply.value?.ok ? reply.value.data?.groups ?? [] : []))
const g = computed(() => groups.value.find((x) => x.id === props.id) ?? null)
const t = computed(() => (g.value ? toksTip(g.value) : null))
const name = computed(() => (props.by === 'role' ? roleLabel(props.id) : props.id))
const what = computed(() => (props.by === 'role' ? roleAbout(props.id) : ''))
const parts = computed(() => (t.value?.mix ?? []).filter((x) => x.value > 0))
const aria = computed(() => `Состав токенов: ${(t.value?.mix ?? []).map((x) => `${x.label} ${x.text}, ${pctText(x.share)}`).join('; ')}`)
const shares = computed(() => {
  if (!g.value) return ''
  const tok = `${pctText(toksShare(g.value, groups.value))} токенов периода`
  return (g.value.usd ?? 0) > 0 ? `${tok} · ${pctText(g.value.share)} стоимости` : `${tok} · без затрат`
})

onMounted(() => {
  store.start()
  if (!reply.value && !store.pending[key.value]) store.request(props.days, props.by)
})

/** Раскрытие сегментов один раз: когда данные группы впервые появились на экране (два кадра, чтобы переход стартовал со свёрнутого состояния). */
const shown = ref(false)
watch(() => !!g.value, (has) => {
  if (has && !shown.value) requestAnimationFrame(() => requestAnimationFrame(() => { shown.value = true }))
}, { immediate: true, flush: 'post' })
</script>

<template>
  <UiDetail label="Расход группы" @close="$emit('close')">
    <template #title><b class="h">{{ name }}</b><span class="sub">{{ panelSubtitle(days, by) }}</span></template>

    <p v-if="what" class="what">{{ what }}</p>

    <template v-if="g && t">
      <section class="hero" aria-label="Токены">
        <div class="total"><b class="num">{{ t.total }}</b><span class="unit">токенов всего, вход и выход</span></div>
        <div class="avg"><b>{{ t.perCall }}</b><span>токенов на вызов</span><small>вход {{ t.perCallIn }}, выход {{ t.perCallOut }}</small></div>
      </section>
      <p class="shares">{{ shares }}</p>

      <section class="mixbox" aria-label="Состав токенов">
        <div class="mix" role="img" :aria-label="aria">
          <i v-for="(p, i) in parts" :key="p.key" class="seg" :class="[p.key, { in: shown }]" :style="{ flex: `${p.share} 1 0`, '--i': i }" />
        </div>
        <ul class="legend">
          <li v-for="p in t.mix" :key="p.key" :class="{ zero: !p.value }">
            <i class="dot" :class="p.key" />
            <span class="lab">{{ p.label }}</span>
            <b>{{ p.text }}</b>
            <span class="pc">{{ pctText(p.share) }}</span>
          </li>
        </ul>
        <p class="note">Рассуждение входит в выход.</p>
      </section>

      <section class="speed" aria-label="Скорость и качество">
        <h3 class="cap">Скорость и качество</h3>
        <dl class="stats">
          <div v-for="s in t.stats" :key="s.key" class="st" :class="{ hot: s.hot }">
            <dd>{{ s.value }}</dd>
            <dt>{{ s.label }} <span class="nt">{{ s.note }}</span></dt>
            <small>{{ s.help }}</small>
          </div>
        </dl>
        <p class="note">Оранжевым выделены ошибки и неверный формат от 15% при 20 и более вызовах.</p>
      </section>

      <section class="price" aria-label="Цена">
        <h3 class="cap">Цена</h3>
        <div class="pr">
          <div><b>{{ t.callCost }}</b><span>за один вызов</span></div>
          <div><b>{{ t.millionCost }}</b><span>за 1 млн токенов</span></div>
        </div>
      </section>
    </template>
    <p v-else-if="loading" class="meta" aria-busy="true">Загружаем расход...</p>
    <p v-else-if="reply && !reply.ok" class="err">Не удалось получить расход: {{ reply.error ?? 'ошибка сервера' }}</p>
    <p v-else class="meta">За этот период по «{{ name }}» вызовов не было.</p>
  </UiDetail>
</template>

<style scoped>
.h { font-size: 17px; overflow-wrap: anywhere; }
.sub { font-size: 12px; color: var(--muted); }
.what { margin: 0; font-size: 13px; line-height: 1.5; color: var(--muted); }
.meta { margin: 0; font-size: 13px; color: var(--muted); }
.err { margin: 0; font-size: 13px; color: var(--neg); }
.cap { margin: 0 0 var(--space-2); font-size: 12px; font-weight: 600; color: var(--muted); }
section, .shares { font-variant-numeric: tabular-nums; }

.hero { display: flex; justify-content: space-between; align-items: flex-end; flex-wrap: wrap; gap: var(--space-3) var(--space-5); }
.total { display: grid; }
.num { font-size: 34px; line-height: 1.1; font-weight: 700; letter-spacing: -0.02em; }
.unit { font-size: 12px; color: var(--muted); }
.avg { display: grid; justify-items: end; text-align: right; }
.avg b { font-size: 18px; font-weight: 600; }
.avg span, .avg small { font-size: 12px; color: var(--muted); }
.shares { margin: calc(var(--space-2) * -1) 0 0; font-size: 12px; color: var(--muted); }

.mixbox { display: grid; gap: var(--space-3); padding-top: var(--space-4); border-top: 1px solid var(--line); }
.mix { display: flex; gap: 2px; height: 12px; }
.seg { display: block; min-width: 2px; border-radius: 2px; transform: scaleX(0); transform-origin: left; }
.seg.in { transform: none; transition: transform 240ms var(--ease-out) calc(var(--i) * 30ms); }
.cache, .dot.cache { background: var(--zone-trades); }
.miss, .dot.miss { background: var(--accent); }
.answer, .dot.answer { background: var(--zone-brain); }
.think, .dot.think { background: var(--zone-market); }
.legend { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-3) var(--space-4); }
.legend li { display: grid; grid-template-columns: 8px minmax(0, 1fr) auto; grid-template-areas: 'dot lab pc' 'dot val pc'; column-gap: 8px; align-items: baseline; }
.legend li.zero { opacity: 0.5; }
.dot { grid-area: dot; align-self: center; width: 8px; height: 8px; border-radius: 2px; }
.lab { grid-area: lab; font-size: 12px; color: var(--muted); }
.legend b { grid-area: val; font-size: 15px; font-weight: 600; }
.pc { grid-area: pc; font-size: 12px; color: var(--muted); }
.note { margin: 0; font-size: 12px; color: var(--muted); line-height: 1.45; }

.speed .note { margin-top: var(--space-3); }
.speed, .price { padding-top: var(--space-4); border-top: 1px solid var(--line); }
.stats { margin: 0; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--space-4); }
.st { display: grid; gap: 1px; align-content: start; min-width: 0; }
.st dd { margin: 0; font-size: 20px; line-height: 1.2; font-weight: 700; }
.st dt { font-size: 12px; }
.st dt .nt { color: var(--muted); }
.st small { font-size: 11px; color: var(--muted); line-height: 1.4; }
.st.hot dd { color: var(--warn); }

.pr { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--space-4); }
.pr div { display: grid; gap: 1px; }
.pr b { font-size: 20px; line-height: 1.2; font-weight: 700; }
.pr span { font-size: 12px; color: var(--muted); }

@media (max-width: 420px) {
  .legend, .stats { grid-template-columns: minmax(0, 1fr); }
}
@media (prefers-reduced-motion: reduce) {
  .seg, .seg.in { transform: none; transition: none; }
}
</style>
