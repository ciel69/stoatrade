import { nextTick, watch } from 'vue'

/**
 * Возврат фокуса клавиатуры после того, как панель изменений исчезла вместе с нажатой кнопкой.
 * arm() просит вернуть фокус; он возвращается на target() (или fallback()), как только нет открытого окна подтверждения (blocked() === false):
 * окно подтверждения само возвращает фокус на кнопку-инициатор, которой к тому времени уже нет, и мешать его ловушке фокуса нельзя.
 * Фокус забирается, только если он потерян (на теле страницы, в исчезнувшем элементе или внутри lostIn), чтобы не отнимать его у того, куда владелец успел перейти сам.
 */
export function useRefocus(opts: { blocked: () => boolean; target: () => HTMLElement | null | undefined; fallback?: () => HTMLElement | null | undefined; lostIn?: () => HTMLElement | null | undefined }) {
  let pending = false
  /** Зона запоминается при arm(): к моменту проверки Vue уже обнулил ссылку на исчезающий элемент, а сам он ещё доигрывает уход. */
  let zone: HTMLElement | null = null

  function lost(): boolean {
    const a = document.activeElement as HTMLElement | null
    if (!a || a === document.body || !a.isConnected) return true
    return !!zone && zone.contains(a)
  }

  function run(): void {
    void nextTick(() => {
      if (!lost()) return
      const t = opts.target()
      const el = t && !(t as HTMLButtonElement).disabled ? t : opts.fallback?.()
      el?.focus({ preventScroll: true })
    })
  }
  function attempt(afterDialog: boolean): void {
    if (!pending || opts.blocked()) return
    pending = false
    // окно подтверждения исчезает плавно и до конца удерживает фокус: даём ему уйти
    if (afterDialog) setTimeout(run, 320)
    else run()
  }

  watch(opts.blocked, () => attempt(true))
  return {
    /** Запомнить зону заранее (при нажатии «Применить»), пока её элемент ещё на месте. */
    mark(): void {
      zone = opts.lostIn?.() ?? zone
    },
    arm(): void {
      pending = true
      zone = opts.lostIn?.() ?? zone
      attempt(false)
    },
  }
}
