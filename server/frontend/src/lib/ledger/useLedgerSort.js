import { computed } from 'vue'

import { useLedgerViewStore } from './useLedgerView.js'

/**
 * The sort of each ledger scope: which column, which direction, and the
 * comparison both scopes sort their rows with.
 *
 * The column and the direction are held in the ledger-view store
 * (./useLedgerView.js), outside both panes, so each scope keeps its own sort
 * across the switch between the two ledgers and across a reload.
 */
export { DEFAULT_LEDGER_SORT, LEDGER_SCOPES } from './useLedgerView.js'

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
  const store = useLedgerViewStore()
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
