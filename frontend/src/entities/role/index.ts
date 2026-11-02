export { useRoleStore } from './model/store'
export { mockAddress, mockModelSet, mockOpts, mockProfile, mockProviderAdd } from './lib/mock'
export {
  ADAPTERS, MAX_SHOWN_MODELS, RE_KEY_FILE, RE_MODEL_NAME, addArgs, addErrors, addLines, addressPatch, addressReflects, emptyAdd, hostOf, envName, filterIds, looksPrivate, modelAddReflects, modelKey, modelsNote, modelsOutcome, netErrorText,
  netReady, newModelSpec, pingLine, pingOutcome, urlProblem,
} from './lib/providerNet'
export type { AddArgs, AddDraft, AddErrors, AddressDraft, ModelsOk, NetFail, NetFinal, PingOk } from './lib/providerNet'
export { createArgs, createReflects, draftOfProvider, editLines, editPatch, editReflects, emptyProviderDraft, normAdapter, providerErrors, slugId } from './lib/forms'
export type { ProviderDraft, ProviderErrors, ProviderPatch } from './lib/forms'
export type { ExchangeRow, ModelPrice, PresetRow, ProfileRow, ProviderModel, ProviderRow, RoleModel, RoleParams, RoleRow, RolesSnapshot, SwapRow } from './model/types'
export {
  MAX_FALLBACK, addFallback, changeLines, changed, chainArgs, chainArgsOf, chainOf, fallbackCandidates, listText, liveLocked, moveFallback, overlay, profileBlock, reflects, removeFallback, reverseLines, setPrimary, targetOf, useArgs, whatText,
} from './lib/chain'
export type { Chain, ChangeLine, ProfileLike } from './lib/chain'
export { groups, issuesOf, pct, placeOf, roleLabel, summary } from './lib/role'
export type { RoleIssue } from './lib/role'
export {
  credText, credsText, ctxText, exchangeStatus, limitsText, modeText, modelsText, perMillion, permsWarning, plural, priceText, providerIssues, rolesUsing, swapBy, swapText, swapWhen, swapsLine,
} from './lib/connect'
export type { ConnectIssue } from './lib/connect'
export {
  EXCHANGE_MODES, activateArgs, activateBlock, activateEffect, activateLine, activateTitle, canSetMode, exchangeBlock, exchangeModeArgs, exchangeModeEffect, exchangeModeLine, exchangeModeTitle, exchangeReflects, keyNote,
} from './lib/exchange'
export {
  LIMIT_FIELDS, REFLECT_WAIT_MS, enableBlock, limitChanges, limitErrors, limitInput, limitText, limitValue, limitsArgs, limitsEqual, limitsWhat, parseLimit, providerReflects, resolveLimits, withLimits,
} from './lib/provider'
export {
  RE_ID, THINKING, draftOfProfile, emptyOpts, emptyProfile, optsArgs, optsErrors, optsLines, optsReflects, paramsText, profileArgs, profileErrors, profileLines,
  profileReflects, thinkingWord,
} from './lib/expert'
export type { Line, OptsArgs, OptsDraft, OptsErrors, ProfileArgs, ProfileDraft, ProfileErrors } from './lib/expert'
export type { Expect, LimitChange, LimitField, LimitKey, Limits, LimitsDraft, ParsedLimit } from './lib/provider'
