import { ref, type Ref } from 'vue'
import { LiveSocket, defaultSocketUrl, fetchLinkInfo, type LinkState } from './live-socket'

/** Один сокет на приложение: слои выше подписываются на темы, состояние связи реактивно. */
let socket: LiveSocket | null = null
const link: Ref<LinkState> = ref('closed')
/** Сервер отказал по числу вкладок (его лимит сокетов исчерпан), а не сеть оборвалась. */
const full: Ref<boolean> = ref(false)

export function useLive(): { socket: LiveSocket; link: Ref<LinkState>; full: Ref<boolean> } {
  if (!socket) {
    socket = new LiveSocket({ url: defaultSocketUrl(), probe: () => fetchLinkInfo() })
    socket.onState((s) => {
      link.value = s
    })
    socket.onRefusal((v) => {
      full.value = v
    })
    // закрытие страницы: закрываем сокет сами, слот на сервере освобождается сразу, а не через ping_timeout
    if (typeof window !== 'undefined') {
      const s = socket
      window.addEventListener('pagehide', () => s.stop())
      window.addEventListener('pageshow', (e) => {
        if ((e as PageTransitionEvent).persisted) s.start()      // возврат из кэша «назад/вперёд»
      })
    }
  }
  return { socket, link, full }
}
