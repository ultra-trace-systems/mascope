import { reactive, watch } from 'vue'
import { defineStore } from 'pinia'

import { ENGINE_UNSTATED, ROLE_BUCKETS, TIERS } from '@/lib/tiers'

/**
 * How each ledger scope is viewed: its sort, its search, its tier chips and
 * every choice of its Show menu, and the one choice both scopes share, whether
 * the listed names are written in full. Held outside the two panes, because
 * PaneBrowserMatch swaps them on whether a sample is focused and a choice held
 * in a pane's own state was lost on every switch; and persisted, so it
 * survives a reload too (docs/dev/assignment_ledger_and_card.md, section 3.4).
 *
 * Every stored value is read back through its key's check: a value the code no
 * longer knows - a sort column, a tier chip, a badge kind, a grouping it has
 * dropped, or a value of the wrong type - falls back to that key's default, and the keys
 * beside it keep theirs.
 *
 * A null sort field is a cleared sort (removableSort's third click on a
 * header) and is kept as one: the ledger then rests in its resting order
 * rather than snapping back to the default column.
 */
export const LEDGER_VIEW_STORAGE_KEY = 'mascope.browser.match.ledgerView'

// Where the sort and the listing choice were kept before this store; read once,
// while the store's own key is absent, so an upgrade keeps them.
const LEGACY_SORT_KEY = 'mascope.browser.match.ledgerSort'
const LEGACY_LISTING_KEY = 'mascope.browser.match.ledgerListing'

export const LEDGER_SCOPES = Object.freeze(['sample', 'batch'])

/** The verdict filter's values; `unverified` is no current verdict. */
export const VERDICT_FILTER_VALUES = Object.freeze([
  'all',
  'confirmed',
  'rejected',
  'unsure',
  'unverified'
])

/**
 * The batch's badge kinds the Show menu filters on one at a time, of the
 * readings the batch ledger draws in its Across the batch column
 * (lib/evidence/marks.js): not the blend and the flat series, which have
 * switches of their own, nor the co-variation, facts and ratio-stability
 * readings, which this filter has never offered.
 */
export const BADGE_FILTER_KINDS = Object.freeze([
  'series',
  'isotopes',
  'persistence',
  'reagent',
  'manual'
])

/**
 * Group by, per scope. Grouping by compound is the sample scope's channel
 * grouping. The batch's groups, by compound and by series, come with the
 * groups step (MAS-1069); until then the batch scope has none to offer.
 */
export const GROUP_BY_OPTIONS = Object.freeze({
  sample: Object.freeze(['none', 'compound']),
  batch: Object.freeze(['none'])
})

/**
 * The columns each scope's ledger sorts on, by the field the table sorts by
 * (a column's `sortField` where it has one). The two panes' specs check that
 * every sortable column is listed here, so a column added or dropped there
 * cannot leave a stored sort the check below misreads.
 */
export const SORT_FIELDS = Object.freeze({
  sample: Object.freeze([
    'sample_peak_mz',
    'sample_peak_intensity',
    'assigned_formula',
    'mech',
    'tierRank',
    'levelRank',
    'verdictRank'
  ]),
  batch: Object.freeze([
    'mz',
    'max_intensity',
    'consensus_formula',
    'consensus_ion_formula',
    'tierRank',
    'memberLevelRank',
    'n_present',
    'acrossRank',
    'verdictRank'
  ])
})

const BUCKETS = new Set([...TIERS, ENGINE_UNSTATED, ...ROLE_BUCKETS])

const isBoolean = (value) => typeof value === 'boolean'
const isPlainObject = (value) => value != null && typeof value === 'object' && !Array.isArray(value)
const isString = (value) => typeof value === 'string'
const oneOf = (values) => (value) => values.includes(value)
const isTierList = (value) =>
  Array.isArray(value) &&
  value.every((key) => BUCKETS.has(key)) &&
  new Set(value).size === value.length

// Each key's default and its check, per scope. A key a scope does not list is
// not a choice of that scope.
const sortFieldOf = (scope) => (value) => value === null || SORT_FIELDS[scope].includes(value)
const COMMON = {
  order: { default: 1, check: oneOf([1, -1]) },
  search: { default: '', check: isString },
  tiers: { default: () => [], check: isTierList },
  isotopologues: { default: false, check: isBoolean },
  roles: { default: true, check: isBoolean },
  engineView: { default: false, check: isBoolean },
  verdict: { default: 'all', check: oneOf(VERDICT_FILTER_VALUES) }
}
const SCHEMA = Object.freeze({
  sample: {
    ...COMMON,
    field: { default: 'sample_peak_intensity', check: sortFieldOf('sample') },
    order: { ...COMMON.order, default: -1 },
    queue: { default: false, check: isBoolean },
    groupBy: { default: 'none', check: oneOf(GROUP_BY_OPTIONS.sample) }
  },
  batch: {
    ...COMMON,
    field: { default: 'max_intensity', check: sortFieldOf('batch') },
    order: { ...COMMON.order, default: -1 },
    stripped: { default: false, check: isBoolean },
    blends: { default: false, check: isBoolean },
    flatSeries: { default: false, check: isBoolean },
    badge: {
      default: null,
      check: (value) => value === null || BADGE_FILTER_KINDS.includes(value)
    },
    engineDisagrees: { default: false, check: isBoolean },
    groupBy: { default: 'none', check: oneOf(GROUP_BY_OPTIONS.batch) }
  }
})

/**
 * The stored view's version. Version 2 made intensity, descending, the default
 * sort of both scopes, where the sample scope's was the tier and the batch
 * scope's the sample count (docs/dev/assignment_ledger_and_card.md, section
 * 7.2). A view saved before it holds the former default as if it were a
 * choice - every key is saved, chosen or not - so a sort equal to a scope's
 * former default is read as no choice and takes the new one. A reader who had
 * picked that very sort loses it once; one who had picked any other keeps it.
 */
export const LEDGER_VIEW_VERSION = 2
const FORMER_DEFAULT_SORT = Object.freeze({
  sample: Object.freeze({ field: 'tierRank', order: 1 }),
  batch: Object.freeze({ field: 'n_present', order: -1 })
})

/**
 * A scope's view as saved at `version`, brought up to this one: the former
 * default sort dropped, so the current default applies.
 */
export function upgradeLedgerView(scope, stored, version) {
  if ((typeof version === 'number' && version >= LEDGER_VIEW_VERSION) || !isPlainObject(stored)) {
    return stored
  }
  const former = FORMER_DEFAULT_SORT[scope]
  if (stored.field !== former.field || (stored.order ?? former.order) !== former.order) {
    return stored
  }
  const rest = { ...stored }
  delete rest.field
  delete rest.order
  return rest
}

const defaultOf = (entry) => (typeof entry.default === 'function' ? entry.default() : entry.default)

/** A scope's view with every key at its default. */
export function defaultLedgerView(scope) {
  return Object.fromEntries(
    Object.entries(SCHEMA[scope]).map(([key, entry]) => [key, defaultOf(entry)])
  )
}

/** The default sort of each scope, as `{ field, order }`. */
export const DEFAULT_LEDGER_SORT = Object.freeze(
  Object.fromEntries(
    LEDGER_SCOPES.map((scope) => {
      const { field, order } = defaultLedgerView(scope)
      return [scope, Object.freeze({ field, order })]
    })
  )
)

/**
 * A scope's stored view over its defaults, key by key: a key absent, or whose
 * value its check refuses, takes its default.
 */
export function restoreLedgerView(scope, stored) {
  const view = defaultLedgerView(scope)
  if (!isPlainObject(stored)) return view
  for (const [key, entry] of Object.entries(SCHEMA[scope])) {
    if (key in stored && entry.check(stored[key])) {
      view[key] = Array.isArray(stored[key]) ? [...stored[key]] : stored[key]
    }
  }
  return view
}

function read(key) {
  try {
    return JSON.parse(localStorage.getItem(key) ?? 'null')
  } catch {
    // Unreadable or absent storage: the defaults apply.
    return null
  }
}

function load() {
  const stored = read(LEDGER_VIEW_STORAGE_KEY)
  if (stored && typeof stored === 'object') return stored
  // Before this store: the sort per scope, and the listing switch.
  const sort = read(LEGACY_SORT_KEY)
  const listing = read(LEGACY_LISTING_KEY)
  return {
    sample: sort?.sample,
    batch: sort?.batch,
    listingFull: listing?.full
  }
}

function save(state) {
  try {
    localStorage.setItem(LEDGER_VIEW_STORAGE_KEY, JSON.stringify(state))
  } catch {
    // Storage refused (quota, private mode): the choices still hold for the session.
  }
}

/** The persisted view of both scopes, and the listing switch they share. */
export const useLedgerViewStore = defineStore('browser.match.ledgerView', () => {
  const stored = load()
  const restore = (scope) =>
    restoreLedgerView(scope, upgradeLedgerView(scope, stored?.[scope], stored?.version))
  const sample = reactive(restore('sample'))
  const batch = reactive(restore('batch'))
  // Listed names folded or in full (Write listed names in full): one switch for
  // both ledgers, so a reader who writes them out in one finds them written out
  // in the other.
  const listing = reactive({ full: stored?.listingFull === true })

  watch(
    [sample, batch, listing],
    () =>
      save({
        version: LEDGER_VIEW_VERSION,
        sample: { ...sample, tiers: [...sample.tiers] },
        batch: { ...batch, tiers: [...batch.tiers] },
        listingFull: listing.full
      }),
    { deep: true }
  )

  return { sample, batch, listing }
})

/** The listing switch both ledgers share: `full`, read and written. */
export const useLedgerListing = () => useLedgerViewStore().listing

/** One scope's view, reactive: read and write its keys. */
export const useLedgerView = (scope) => useLedgerViewStore()[scope]
