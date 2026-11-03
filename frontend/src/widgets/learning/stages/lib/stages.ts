import { blockView, type LearningBlock } from '@/entities/learning'

/** Заглушки блоков, которых ещё нет: заголовок, «Появится на этапе N» и одна строка о том, что в блоке будет. */
export interface StageView {
  id: string
  title: string
  /** «Появится на этапе 2» */
  stage: string
  /** одна строка описания */
  about: string
}

const ABOUT: Readonly<Record<string, string>> = {
  lessons: 'Какие гипотезы мозга подтвердились, какие отвергнуты и сколько сделок за каждой стоит.',
  versions: 'Как менялся результат до и после смены промпта или правил мозга.',
  hyps: 'Какие гипотезы мозга набрали 30 сделок на бумаге и подтвердились, а какие ещё копят данные.',
}
const ABOUT_FALLBACK = 'Здесь появится ещё один срез обучения мозга.'

export function stageView(b: LearningBlock): StageView {
  return { id: b.id, title: b.title, stage: blockView(b).message, about: ABOUT[b.id] ?? ABOUT_FALLBACK }
}

/** Блоки-заглушки в порядке страницы; блоки с данными и ошибками сюда не попадают. */
export const stageBlocks = (blocks: readonly LearningBlock[]): StageView[] => blocks.filter((b) => b.status === 'stage').map(stageView)
