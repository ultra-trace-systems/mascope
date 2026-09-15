import { reactive, watch } from 'vue'
import { defineStore } from 'pinia'

/**
 * The sort of each ledger in the match browser: which column, which direction.
 *
 * The batch ledger and the sample ledger are two panes that PaneBrowserMatch
 * swaps on whether a sample is focused, so a sort held in a pane's own state
 * was lost on every switch: sort the sample ledger by intensity, step out to
 * the batch ledger, step back in, and it was by tier again. Held here, outside
 * both panes, each ledger keeps its own sort across the switch - and across a
 * reload, persisted the way the peak browser persists its sort.
 *
 * A null field is a cleared sort (removableSort's third click on a header) and
 * is kept as one: the ledger then rests in its confidence order rather than
 * snapping back to the default column.
 */
const STORAGE_KEY = 'mascope.browser.match.ledgerSort'

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

/** A ledger's stored sort over its default, key by key, so a cleared field stays cleared. */
function restore(ledger, stored) {
  const defaults = DEFAULT_LEDGER_SORT[ledger]
  const has = (key) => stored != null && typeof stored === 'object' && key in stored
  return {
    field: has('field') ? stored.field : defaults.field,
    order: has('order') ? stored.order : defaults.order
  }
}

export const useLedgerSort = defineStore('browser.match.ledgerSort', () => {
  const stored = load()
  const sample = reactive(restore('sample', stored.sample))
  const batch = reactive(restore('batch', stored.batch))

  watch([sample, batch], () => save({ sample: { ...sample }, batch: { ...batch } }), {
    deep: true
  })

  return { sample, batch }
})
