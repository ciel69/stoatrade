import { ref, type Ref } from 'vue'
import { LiveSocket, defaultSocketUrl, type LinkState } from './live-socket'

/** Один сокет на приложение: слои выше подписываются на темы, состояние связи реактивно. */
let socket: LiveSocket | null = null
const link: Ref<LinkState> = ref('closed')

export function useLive(): { socket: LiveSocket; link: Ref<LinkState> } {
  if (!socket) {
    socket = new LiveSocket({ url: defaultSocketUrl() })
    socket.onState((s) => {
      link.value = s
    })
  }
  return { socket, link }
}
