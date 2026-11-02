export { useRoleStore } from './model/store'
export { mockOpts, mockPresetAdd, mockProfile } from './lib/mock'
export type { ExchangeRow, ModelPrice, PresetRow, ProfileRow, ProviderModel, ProviderRow, RoleModel, RoleParams, RoleRow, RolesSnapshot, SwapRow } from './model/types'
export {
  MAX_FALLBACK, addFallback, changeLines, changed, chainArgs, chainArgsOf, chainOf, fallbackCandidates, listText, liveLocked, moveFallback, overlay, profileBlock, reflects, removeFallback, reverseLines, setPrimary, targetOf, useArgs, whatText,
} from './lib/chain'
export type { Chain, ChangeLine, ProfileLike } from './lib/chain'
export { groups, issuesOf, pct, placeOf, roleLabel, summary } from './lib/role'
export type { RoleIssue } from './lib/role'
export {
  connectSummary, credText, credsText, ctxText, exchangeStatus, limitsText, modeText, modelsText, perMillion, permsWarning, plural, priceText, providerIssues, rolesUsing, swapBy, swapText, swapWhen, swapsLine,
} from './lib/connect'
export type { ConnectIssue } from './lib/connect'
export {
  EXCHANGE_MODES, activateArgs, activateBlock, activateEffect, activateLine, activateTitle, canSetMode, exchangeBlock, exchangeModeArgs, exchangeModeEffect, exchangeModeLine, exchangeModeTitle, exchangeReflects, keyNote,
} from './lib/exchange'
export {
  LIMIT_FIELDS, REFLECT_WAIT_MS, enableBlock, limitChanges, limitErrors, limitInput, limitText, limitValue, limitsArgs, limitsEqual, limitsWhat, parseLimit, providerReflects, resolveLimits, withLimits,
} from './lib/provider'
export {
  RE_ID, THINKING, draftOfProfile, emptyOpts, emptyProfile, optsArgs, optsErrors, optsLines, optsReflects, paramsText, presetArgs, presetNote, presetReflects, presetsToAdd, profileArgs, profileErrors, profileLines,
  profileReflects, remarkText, thinkingWord,
} from './lib/expert'
export type { Line, OptsArgs, OptsDraft, OptsErrors, ProfileArgs, ProfileDraft, ProfileErrors } from './lib/expert'
export type { Expect, LimitChange, LimitField, LimitKey, Limits, LimitsDraft, ParsedLimit } from './lib/provider'
