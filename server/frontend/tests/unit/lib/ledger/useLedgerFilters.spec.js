import { describe, it, expect } from 'vitest'
import { nextTick, ref } from 'vue'
import { FilterMatchMode, FilterOperator } from '@primevue/core/api'

import { ledgerTierChips, useLedgerFilters } from '@/lib/ledger/useLedgerFilters.js'

const ROWS = [
  { id: 1, bucket: 'assigned', formula: 'C6H6', level: '4a' },
  { id: 2, bucket: 'candidate', formula: 'C7H8', level: '5a' },
  { id: 3, bucket: 'unassigned', formula: null, level: null },
  { id: 4, bucket: 'reagent', formula: 'NO3-', level: null }
]
const shown = (passes, options) => ROWS.filter((row) => passes(row, options)).map((row) => row.id)

describe('ledgerTierChips', () => {
  it('lists the tiers in confidence order, then the roles, with their counts', () => {
    const chips = ledgerTierChips({ assigned: 2, reagent: 1 })
    expect(chips.map((chip) => [chip.key, chip.label, chip.count, chip.role])).toEqual([
      ['assigned', 'assigned', 2, false],
      ['candidate', 'candidate', 0, false],
      ['below_assignability', 'below', 0, false],
      ['unassigned', 'unassigned', 0, false],
      ['reagent', 'reagent', 1, true],
      ['artifact', 'artifact', 0, true]
    ])
  })

  it("adds the engine view's not-stated chip after the tiers", () => {
    const keys = ledgerTierChips({}, { engineView: true }).map((chip) => chip.key)
    expect(keys.indexOf('engine_unstated')).toBe(4)
    expect(ledgerTierChips({}, { engineView: true })[4].label).toBe('not stated')
  })
})

describe('useLedgerFilters', () => {
  it('filters by every chip that is on, and by none while none is', () => {
    const { toggleTier, passes, activeTiers } = useLedgerFilters({ bucketOf: (row) => row.bucket })
    expect(shown(passes)).toEqual([1, 2, 3, 4])
    toggleTier('assigned')
    expect(shown(passes)).toEqual([1])
    toggleTier('reagent')
    expect(shown(passes)).toEqual([1, 4])
    toggleTier('assigned')
    expect(shown(passes)).toEqual([4])
    toggleTier('reagent')
    expect(activeTiers.size).toBe(0)
    expect(shown(passes)).toEqual([1, 2, 3, 4])
  })

  it('drops the chips when the view they name buckets of changes', async () => {
    const view = ref(false)
    const { toggleTier, activeTiers } = useLedgerFilters({ bucketOf: (row) => row.bucket, view })
    toggleTier('candidate')
    view.value = true
    await nextTick()
    expect(activeTiers.size).toBe(0)
  })

  it("applies a column menu through PrimeVue's match modes, read through the scope's reader", () => {
    const { filters, passes } = useLedgerFilters({
      bucketOf: (row) => row.bucket,
      menus: { shown_formula: FilterMatchMode.CONTAINS },
      valueOf: { shown_formula: (row) => row.formula }
    })
    filters.value.shown_formula.constraints[0].value = 'C'
    expect(shown(passes)).toEqual([1, 2])
  })

  it("joins a menu's rules with its operator", () => {
    const { filters, passes } = useLedgerFilters({
      bucketOf: (row) => row.bucket,
      menus: { formula: FilterMatchMode.CONTAINS }
    })
    filters.value.formula = {
      operator: FilterOperator.OR,
      constraints: [
        { value: 'C6', matchMode: FilterMatchMode.CONTAINS },
        { value: 'NO3', matchMode: FilterMatchMode.CONTAINS }
      ]
    }
    expect(shown(passes)).toEqual([1, 4])
    filters.value.formula.operator = FilterOperator.AND
    expect(shown(passes)).toEqual([])
  })

  // Left on while its column is absent, a menu would empty the table for a
  // reason nothing on screen shows.
  it('filters by a menu only while its column is shown, and drops it when the column goes', async () => {
    const hasLevels = ref(true)
    const { filters, passes } = useLedgerFilters({
      bucketOf: (row) => row.bucket,
      menus: { level: FilterMatchMode.EQUALS },
      menuShown: { level: hasLevels }
    })
    filters.value.level.constraints[0].value = '4a'
    expect(shown(passes)).toEqual([1])
    hasLevels.value = false
    expect(shown(passes)).toEqual([1, 2, 3, 4])
    await nextTick()
    expect(filters.value.level.constraints[0].value).toBeNull()
  })

  it('filters on the verdict a row carries, unless asked to leave verdicts out', () => {
    const verdicts = { 1: { verdict: 'confirmed' }, 2: { verdict: 'rejected' } }
    const { verdictFilter, passes } = useLedgerFilters({
      bucketOf: (row) => row.bucket,
      verdictOf: (row) => verdicts[row.id] ?? null
    })
    verdictFilter.value = 'confirmed'
    expect(shown(passes)).toEqual([1])
    verdictFilter.value = 'unverified'
    expect(shown(passes)).toEqual([3, 4])
    expect(shown(passes, { verdicts: false })).toEqual([1, 2, 3, 4])
  })
})
