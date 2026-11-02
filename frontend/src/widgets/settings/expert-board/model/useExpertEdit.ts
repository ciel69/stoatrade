import { computed, ref } from 'vue'
import { useActionStore } from '@/entities/action'
import {
  draftOfProfile, emptyProfile, mockPresetAdd, mockProfile, presetArgs, presetReflects, presetsToAdd, profileArgs, profileErrors, profileLines, profileReflects, useRoleStore,
  type PresetRow, type ProfileArgs, type ProfileDraft, type ProfileRow,
} from '@/entities/role'
import { useReflect } from '@/shared/lib'

/**
 * Правка «для специалиста»: профиль модели (brain.profile set) и провайдер из шаблона (llm.provider add). Оба действия идут через общий движок (нужен ли диалог «Принимаю риски», решает сервер),
 * после успеха ждём, пока тема settings отразит изменение. Пустое поле параметра значит «не менять». Удаления профилей и провайдеров здесь нет.
 */
export function useExpertEdit() {
  const store = useRoleStore()
  const action = useActionStore()
  const busy = computed(() => action.active)
  const snap = computed(() => store.snapshot)
  const profiles = computed(() => snap.value?.profile_list ?? [])
  const providers = computed(() => snap.value?.provider_list ?? [])
  const presets = computed(() => presetsToAdd(snap.value?.preset_list))
  const remarks = computed(() => snap.value?.errors ?? [])
  const canProfile = computed(() => action.can('brain.profile'))
  const canPreset = computed(() => action.can('llm.provider'))

  // ---- профиль
  /** null: профиль не выбран; '' (пустое имя): создаётся новый */
  const editing = ref<string | null>(null)
  const draft = ref<ProfileDraft>(emptyProfile())
  const profileWait = useReflect()
  const existing = computed<ProfileRow | null>(() => (editing.value ? profiles.value.find((p) => p.id === editing.value) ?? null : null))
  const isNew = computed(() => editing.value === '')
  const ids = computed(() => profiles.value.map((p) => p.id))
  const errors = computed(() => profileErrors(draft.value, existing.value, ids.value))
  const pArgs = computed(() => profileArgs(draft.value, existing.value, ids.value))
  const providerName = (id: string): string => providers.value.find((p) => p.id === id)?.name ?? id
  const modelName = (prov: string, model: string): string => providers.value.find((p) => p.id === prov)?.models.find((m) => m.id === model)?.name ?? model
  const lines = computed(() => (pArgs.value ? profileLines(draft.value, existing.value, providerName, modelName) : []))
  const models = computed(() => providers.value.find((p) => p.id === draft.value.provider)?.models ?? [])

  function openProfile(id: string): void {
    const p = profiles.value.find((x) => x.id === id)
    if (!p) return
    editing.value = id
    draft.value = draftOfProfile(p)
  }
  function openNew(): void {
    editing.value = ''
    draft.value = emptyProfile()
  }
  function closeProfile(): void {
    editing.value = null
  }
  function setProvider(id: string): void {
    const keep = providers.value.find((p) => p.id === id)?.models.some((m) => m.id === draft.value.model)
    draft.value = { ...draft.value, provider: id, model: keep ? draft.value.model : '' }
  }
  function submitProfile(): boolean {
    const a: ProfileArgs | null = pArgs.value
    if (busy.value || !a || !canProfile.value) return false
    return action.run({
      action: 'brain.profile', args: { ...a }, title: isNew.value ? `Новый профиль «${a.id}»` : `Профиль «${a.id}»`,
      what: lines.value.map((l) => `${l.label.toLowerCase()}: ${l.from} → ${l.to}`).join('; ') + '.',
      onDone: () => {
        editing.value = null
        profileWait.begin(() => profileReflects(snap.value?.profile_list, a))
        if (store.mocked) store.mockExpertApply((s) => mockProfile(s, a))
      },
    })
  }

  // ---- провайдер из шаблона
  const presetWait = useReflect()
  function addPreset(p: PresetRow): boolean {
    if (busy.value || !canPreset.value) return false
    return action.run({
      action: 'llm.provider', args: { ...presetArgs(p) }, title: `Добавить провайдера «${p.name}»`,
      what: 'Провайдер появится в списке выключенным и без ключа: включите его и добавьте ключ в его панели.',
      onDone: () => {
        presetWait.begin(() => presetReflects(snap.value?.provider_list, p.id))
        if (store.mocked) store.mockExpertApply((s) => mockPresetAdd(s, p.id))
      },
    })
  }

  return {
    snap, busy, profiles, providers, presets, remarks, canProfile, canPreset, editing, draft, existing, isNew, errors, pArgs, lines, models, profileWait, presetWait,
    openProfile, openNew, closeProfile, setProvider, submitProfile, addPreset,
  }
}

export type ExpertEdit = ReturnType<typeof useExpertEdit>
