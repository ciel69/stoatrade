export { useExperimentStore } from './model/store'
export type { ExpConfirmed, ExpDetail, ExpMeta, ExpMode, ExpRow, ExpTrade } from './model/types'
export {
  DEFAULT_MIN_N, expHeadline, expState, groupFacts, groupTitle, hiddenRows, rowKey, rowViews, sortRows, splitKey, thresholdText, tradeViews, tradesText,
} from './lib/experiment'
export type { ExpHeadline, ExpRowView, ExpState, ExpTradeView, Tone } from './lib/experiment'
export { EXP_MOCKS, isExpMock, mockExp } from './lib/mock'
export type { ExpMock } from './lib/mock'
export { default as ExpGroupPanel } from './ui/ExpGroupPanel.vue'
