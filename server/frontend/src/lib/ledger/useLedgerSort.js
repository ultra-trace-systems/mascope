import { computed, reactive, watch } from 'vue'
import { defineStore } from 'pinia'

/**
 * The sort of each ledger scope: which column, which direction, and the
 * comparison both scopes sort their rows with.
 *
 * The batch ledger and the sample ledger are two panes that PaneBrowserMatch
 * swaps on whether a sample is focused, so a sort held in a pane's own state
 * was lost on every switch: sort the sample ledger by intensity, step out to
 * the batch ledger, step back in, and it was by tier again. Held here, outside
 * both panes, each scope keeps its own sort across the switch - and across a
 * reload, persisted the way the peak browser persists its sort.
 *
 * A null field is a cleared sort (removableSort's third click on a header) and
 * is kept as one: the ledger then rests in its resting order rather than
 * snapping back to the default column.
 */
const STORAGE_KEY = 'mascope.browser.match.ledgerSort'

export const LEDGER_SCOPES = Object.freeze(['sample', 'batch'])

export const DEFAULT_LEDGER_SORT = Object.freeze({
  sample: Object.freeze({ field: 'tierRank', order: 1 }),
  batch: Object.freeze({ field: 'n_present', order: -1 })
})

function load() {
  try {
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? 'null')
    if (stored && typeof stored === 'object') return stored
  } catch {
    // Unreadable or absent storage: the defaults apply.
  }
  return {}
}

function save(state) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state))
  } catch {
    // Storage refused (quota, private mode): the sort still holds for the session.
  }
}

/** A scope's stored sort over its default, key by key, so a cleared field stays cleared. */
function restore(scope, stored) {
  const defaults = DEFAULT_LEDGER_SORT[scope]
  const has = (key) => stored != null && typeof stored === 'object' && key in stored
  return {
    field: has('field') ? stored.field : defaults.field,
    order: has('order') ? stored.order : defaults.order
  }
}

/** The persisted sort of both scopes. */
export const useLedgerSortStore = defineStore('browser.match.ledgerSort', () => {
  const stored = load()
  const sample = reactive(restore('sample', stored.sample))
  const batch = reactive(restore('batch', stored.batch))

  watch([sample, batch], () => save({ sample: { ...sample }, batch: { ...batch } }), {
    deep: true
  })

  return { sample, batch }
})

// Numeric collation, so the formula column reads as a chemist expects: C2H6
// before C10H22, not after it. This is the comparer PrimeVue sorted with -
// @primeuix/utils' localeComparator() is
// `new Intl.Collator(undefined, { numeric: true }).compare` - and taking the
// sort over must not quietly change the order it used to produce.
const collator = new Intl.Collator(undefined, { numeric: true })

// Missing values sort last in both directions - a peak with no formula, or no
// intensity, is unknown rather than "smallest" - counting the empty string as
// missing, which is what PrimeVue's isEmpty() did.
export const isBlank = (value) => value == null || value === ''

/**
 * Compare two rows on one column, blanks last in both directions.
 *
 * @param {string} field the column's field
 * @param {number} order 1 ascending, -1 descending
 * @param {Function} valueOf reads the sorted value off a row
 * @returns {Function} a comparator
 */
export function compareBy(field, order, valueOf = (row) => row[field]) {
  const dir = order === -1 ? -1 : 1
  return (a, b) => {
    const av = valueOf(a)
    const bv = valueOf(b)
    if (isBlank(av) && isBlank(bv)) return 0
    if (isBlank(av)) return 1
    if (isBlank(bv)) return -1
    if (typeof av === 'string' && typeof bv === 'string') return collator.compare(av, bv) * dir
    if (av < bv) return -dir
    if (av > bv) return dir
    return 0
  }
}

/**
 * One scope's handles on its sort, and the sort itself.
 *
 * The sort is the ledger's, not the table's. PrimeVue sorts the flat row array
 * it is handed, which tears every isotopologue away from the parent it belongs
 * under the moment the ledger is sorted by anything but the default. `lazy`
 * hands sorting back, so a scope sorts its top-level rows with `sort` and
 * re-attaches each family underneath. The header still renders its indicator
 * and emits the field it was clicked with, through `field` and `order`.
 *
 * @param {'sample'|'batch'} scope which ledger
 * @param {Object<string, Function>} [values] what a column sorts on, where that
 *   is not the field it is named by
 * @returns {{ field: import('vue').Ref, order: import('vue').Ref, sort: Function }}
 */
export function useLedgerSort(scope, values = {}) {
  const store = useLedgerSortStore()
  const field = computed({
    get: () => store[scope].field,
    set: (value) => (store[scope].field = value)
  })
  const order = computed({
    get: () => store[scope].order,
    set: (value) => (store[scope].order = value)
  })

  /**
   * Sort rows in place by the chosen column, `resting` breaking ties - and
   * ordering them alone while no column is chosen.
   */
  function sort(rows, resting) {
    const key = field.value
    const byColumn = key ? compareBy(key, order.value, values[key]) : null
    return rows.sort(byColumn ? (a, b) => byColumn(a, b) || resting(a, b) : resting)
  }

  return { field, order, sort }
}
