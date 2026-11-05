import { ref, watch } from 'vue'
import { defineStore } from 'pinia'

/**
 * Whether the ledgers show the "listed as" column in full or folded to a mark.
 *
 * The full column - the name, its list and the list's tags - made the ledger
 * too wide for the column the browser has, so it rests folded: a flask with the
 * list's tags beside it, the rest in its tooltip. One switch for both ledgers,
 * held outside them as their sort is (./ledgerSort.js): PaneBrowserMatch swaps
 * the two panes, and a reader who opened the column in one would otherwise find
 * it folded again in the other. Persisted, so it survives a reload too.
 */
const STORAGE_KEY = 'mascope.browser.match.ledgerListing'

function load() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) ?? 'null')?.full === true
  } catch {
    // Unreadable or absent storage: folded, the default.
    return false
  }
}

function save(full) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ full }))
  } catch {
    // Storage refused (quota, private mode): the choice still holds for the session.
  }
}

export const useLedgerListing = defineStore('browser.match.ledgerListing', () => {
  const full = ref(load())
  watch(full, save)
  return { full }
})
