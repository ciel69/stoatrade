import { computed, ref } from 'vue'
import { useActionStore } from '@/entities/action'
import {
  draftOfProfile, emptyProfile, mockProfile, profileArgs, profileErrors, profileLines, slugId, useRoleStore,
  type ProfileDraft, type ProfileErrors,
} from '@/entities/role'

/**
 * Одна форма профиля на «новый» и «изменить» (brain.profile set). Профиль это модель с настройками ответа; роли выбирают профили для себя. Новому профилю человек даёт название,
 * служебное имя (id) строится из него само. Пустое поле параметра значит «по умолчанию»: при создании ничего не отправляется, при правке прежнее значение остаётся
 * (сервер не умеет снять отдельный параметр). Для нового профиля заранее выбраны первый включённый провайдер и его первая модель. Подтверждение рисков, если оно нужно, показывает общий диалог.
 */
export function useProfileForm(profileId: () => string | null, onDone: () => void) {
  const store = useRoleStore()
  const action = useActionStore()
  const snap = computed(() => store.snapshot)
  const profiles = computed(() => snap.value?.profile_list ?? [])
  const providers = computed(() => snap.value?.provider_list ?? [])
  const ids = computed(() => profiles.value.map((p) => p.id))
  const existing = computed(() => { const id = profileId(); return id ? profiles.value.find((p) => p.id === id) ?? null : null })
  const isNew = computed(() => profileId() === null)
  const canEdit = computed(() => action.can('brain.profile'))
  const busy = computed(() => action.active)

  const first = providers.value.find((p) => p.enabled) ?? providers.value[0]
  const name = ref('')
  const draft = ref<ProfileDraft>(existing.value ? draftOfProfile(existing.value) : { ...emptyProfile(), provider: first?.id ?? '', model: first?.models[0]?.id ?? '' })
  const failure = ref('')

  /** служебное имя: у нового из названия, у существующего прежнее */
  const id = computed(() => (isNew.value ? slugId(name.value, ids.value, 'profile') : (existing.value?.id ?? '')))
  const full = computed<ProfileDraft>(() => ({ ...draft.value, id: id.value }))
  const errors = computed<ProfileErrors & { name?: string }>(() => {
    const e: ProfileErrors & { name?: string } = profileErrors(full.value, existing.value, ids.value)
    delete e.id
    if (isNew.value && !name.value.trim()) e.name = 'Введите название.'
    else if (isNew.value && name.value.trim().length > 60) e.name = 'Не длиннее 60 знаков.'
    return e
  })
  const args = computed(() => (Object.keys(errors.value).length ? null : profileArgs(full.value, existing.value, ids.value)))
  const providerName = (pid: string): string => providers.value.find((p) => p.id === pid)?.name ?? pid
  const modelName = (pid: string, m: string): string => providers.value.find((p) => p.id === pid)?.models.find((x) => x.id === m)?.name ?? m
  const lines = computed(() => (args.value ? profileLines(full.value, existing.value, providerName, modelName) : []))
  const models = computed(() => providers.value.find((p) => p.id === draft.value.provider)?.models ?? [])
  const provider = computed(() => providers.value.find((p) => p.id === draft.value.provider) ?? null)
  const canSubmit = computed(() => !busy.value && canEdit.value && !!args.value)

  function setProvider(pid: string): void {
    const p = providers.value.find((x) => x.id === pid)
    const keep = p?.models.some((m) => m.id === draft.value.model)
    draft.value = { ...draft.value, provider: pid, model: keep ? draft.value.model : (p?.models[0]?.id ?? '') }
    failure.value = ''
  }
  function patch(p: Partial<ProfileDraft>): void {
    draft.value = { ...draft.value, ...p }
    failure.value = ''
  }

  function submit(): boolean {
    const a = args.value
    if (!canSubmit.value || !a) return false
    failure.value = ''
    const title = isNew.value ? `Новый профиль «${name.value.trim()}»` : `Профиль «${a.id}»`
    return action.run({
      action: 'brain.profile', args: { ...a }, title, what: lines.value.map((l) => `${l.label.toLowerCase()}: ${l.from} → ${l.to}`).join('; ') + '.',
      onDone: () => {
        if (store.mocked) store.mockExpertApply((s) => mockProfile(s, a))
        onDone()
      },
      onFail: (t, f) => { failure.value = f?.code === 'E_ARGS' && f.msg ? `${t} Платформа ответила: ${f.msg.replace(/\s+/g, ' ').slice(0, 200)}` : t },
    })
  }

  return { existing, isNew, name, draft, id, errors, args, lines, models, provider, providers, canEdit, busy, canSubmit, failure, setProvider, patch, submit }
}

export type ProfileForm = ReturnType<typeof useProfileForm>
