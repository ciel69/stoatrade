import { computed, onMounted } from 'vue'
import { useBrainStore } from '@/entities/brain'
import { useControlStore } from '@/entities/control'
import { useDayStore } from '@/entities/day'
import { useNewsStore } from '@/entities/headline'
import { useMarketStore } from '@/entities/market'
import { useSystemStore } from '@/entities/system'
import { useTradingStore } from '@/entities/trading'
import { useLive } from '@/shared/api'
import { useNow } from '@/shared/lib'
import { buildRows } from '../lib/rows'
import { mockKind, mockStatus } from '../lib/mock'
import { summarize } from '../lib/summary'
import type { StatusInput } from '../lib/types'

/** Состояние платформы для индикатора в шапке: темы приходят по WebSocket (опроса нет), возраст считается по часам клиента. ?status=mock|mock-warn|mock-bad: образцовые данные без сервера. */
export function usePlatformStatus() {
  const trading = useTradingStore()
  const system = useSystemStore()
  const brain = useBrainStore()
  const market = useMarketStore()
  const news = useNewsStore()
  const control = useControlStore()
  const day = useDayStore()
  const { link } = useLive()
  const now = useNow()
  const mock = mockKind(location.search)
  const mockBase = Date.now() / 1000
  const mocked = mock ? mockStatus(mock, mockBase) : null

  onMounted(() => {
    trading.start()
    system.start()
    brain.start()
    market.start()
    news.start()
    control.start()
    day.start()
    // образец без сервера: те же данные видят и другие блоки страницы «Система» (баннер, готовность к живой торговле, узлы)
    if (mocked) {
      if (mocked.system) system.apply({ topic: 'system', seq: 0, ts: 0, data: mocked.system })
      if (mocked.ctl) control.applyCtl({ topic: 'ctl', seq: 0, ts: 0, data: mocked.ctl })
    }
  })

  const input = computed<StatusInput>(() => mocked
    ? { ...mocked, now: now.value }
    : { now: now.value, link: link.value, home: trading.snapshot, system: system.snapshot, brain: brain.snapshot, market: market.snapshot, news: news.snapshot, ctl: control.ctl, day: day.snapshot })
  const summary = computed(() => {
    const i = input.value
    return summarize(buildRows(i), { mode: i.ctl?.platform.mode ?? i.system?.mode ?? i.home?.platform.mode ?? null })
  })
  return { summary }
}
