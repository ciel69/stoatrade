export { useLearningStore } from './model/store'
export { BLOCK_STATUSES, LEARNING_MODES, isLearningMode } from './model/types'
export type {
  BlockStatus, DisciplineBlock, DisciplineWeek, EconomicsBlock, EconomicsWeek, Est, ExitsBlock, ExitsWindow, LearningBlock, LearningDetail, LearningMeta, LearningMode, LearningVerdict,
  MemoryBlock, PolicyRow, ResultBlock, SeriesPoint, SkipsBlock, SkipsWeek, Trend, VerdictGuards, VerdictState, VsPolicyBlock, WindowStat,
  BestLesson, HypFamily, HypRef, HypStatus, HypsBlock, LessonsBlock, LessonsByStatus, LessonsFunnel, LessonsWeek, RulebookPair, SwapPair, VersionPairBase, VersionSide,
  VersionVerdict, VersionsBlock,
} from './model/types'
export {
  COMPUTING_GIVE_UP_S, DEFAULT_ORDER, MIN_N, SCHEMA, blockView, estView, failedText, isSmall, isStale, learningState, normalizeLearning, plainView, toneOf, updatedText, verdictView,
} from './lib/learning'
export type { BlockKind, BlockView, EstKind, EstView, LearningState, Tone, VerdictView } from './lib/learning'
export { LEARNING_MOCKS, isLearningMock, mockLearning } from './lib/mock'
export type { LearningMock } from './lib/mock'
