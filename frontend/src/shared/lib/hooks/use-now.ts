import { onMounted, onUnmounted, ref, type Ref } from 'vue'

/** Текущее время в секундах (epoch), обновляется раз в секунду; один общий таймер на все виджеты, живёт, пока есть хотя бы один пользователь. */
const now = ref(Date.now() / 1000)
let users = 0
let timer: ReturnType<typeof setInterval> | null = null

export function useNow(): Ref<number> {
  onMounted(() => {
    now.value = Date.now() / 1000
    if (!users++) timer = setInterval(() => { now.value = Date.now() / 1000 }, 1000)
  })
  onUnmounted(() => {
    if (!--users && timer) {
      clearInterval(timer)
      timer = null
    }
  })
  return now
}
