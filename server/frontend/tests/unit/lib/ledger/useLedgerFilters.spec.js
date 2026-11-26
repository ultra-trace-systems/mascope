import { describe, it, expect } from 'vitest'
import { nextTick, reactive, ref } from 'vue'
import { FilterMatchMode, FilterOperator } from '@primevue/core/api'

import { ledgerTierChips, useLedgerFilters } from '@/lib/ledger/useLedgerFilters.js'

const ROWS = [
  { id: 1, bucket: 'assigned', formula: 'C6H6', level: '4a' },
  { id: 2, bucket: 'candidate', formula: 'C7H8', level: '5a' },
  { id: 3, bucket: 'unassigned', formula: null, level: null },
  { id: 4, bucket: 'reagent', formula: 'NO3-', level: null }
]
const shown = (passes, options) => ROWS.filter((row) => passes(row, options)).map((row) => row.id)

const ALL = {
  assigned: 2,
  candidate: 1,
  below_assignability: 1,
  unassigned: 3,
  reagent: 1,
  artifact: 1
}

describe('ledgerTierChips', () => {
  it('lists the tiers in confidence order, then the roles, with their counts', () => {
    const chips = ledgerTierChips(ALL)
    expect(chips.map((chip) => [chip.key, chip.label, chip.count, chip.role])).toEqual([
      ['assigned', 'assigned', 2, false],
      ['candidate', 'candidate', 1, false],
      ['below_assignability', 'below', 1, false],
      ['unassigned', 'unassigned', 3, false],
      ['reagent', 'reagent', 1, true],
      ['artifact', 'artifact', 1, true]
    ])
  })

  it('hides a chip with nothing in it, unless it is on', () => {
    const counts = { assigned: 2, reagent: 1 }
    expect(ledgerTierChips(counts).map((chip) => chip.key)).toEqual(['assigned', 'reagent'])
    // An active chip stays, so the filter it holds can be seen and turned off.
    const active = new Set(['candidate'])
    expect(ledgerTierChips(counts, { active }).map((chip) => [chip.key, chip.count])).toEqual([
      ['assigned', 2],
      ['candidate', 0],
      ['reagent', 1]
    ])
  })

  it("adds the engine view's not-stated chip after the tiers", () => {
    const chips = ledgerTierChips({ ...ALL, engine_unstated: 4 }, { engineView: true })
    const keys = chips.map((chip) => chip.key)
    expect(keys.indexOf('engine_unstated')).toBe(4)
    expect(chips[4].label).toBe('not stated')
    expect(ledgerTierChips({ engine_unstated: 4 }).map((chip) => chip.key)).toEqual([])
  })

  it('leaves the roles out while their rows are hidden', () => {
    expect(ledgerTierChips(ALL, { roles: false }).some((chip) => chip.role)).toBe(false)
  })
})

describe('useLedgerFilters', () => {
  // What lets a ledger say which filter hides a row (the note after a hand
  // assignment, PaneBrowserAssignment.vue): the first filter on that hides
  // it, in the order passes() reads them.
  it('names the filter that hides a row, and none for a row that passes', () => {
    const state = reactive({ tiers: [], search: '', roles: true, verdict: 'all' })
    const verdicts = { 1: 'confirmed' }
    const { toggleTier, hiddenBy, passes, filters } = useLedgerFilters({
      bucketOf: (row) => row.bucket,
      state,
      menus: { level: FilterMatchMode.EQUALS },
      verdictOf: (row) => (verdicts[row.id] ? { verdict: verdicts[row.id] } : null),
      search: { texts: (row) => [row.formula], mzs: () => [] }
    })
    expect(ROWS.map((row) => hiddenBy(row))).toEqual([null, null, null, null])

    state.roles = false
    expect(hiddenBy(ROWS[3])).toBe('roles')
    state.roles = true
    toggleTier('assigned')
    expect(hiddenBy(ROWS[1])).toBe('tiers')
    expect(hiddenBy(ROWS[0])).toBeNull()
    toggleTier('assigned')
    state.search = 'C7H8'
    expect(hiddenBy(ROWS[0])).toBe('search')
    state.search = ''
    filters.value.level.constraints[0].value = '5a'
    expect(hiddenBy(ROWS[0])).toBe('menu:level')
    filters.value.level.constraints[0].value = null
    state.verdict = 'confirmed'
    expect(hiddenBy(ROWS[1])).toBe('verdict')
    expect(hiddenBy(ROWS[1], { verdicts: false })).toBeNull()
    // passes() is hiddenBy's null.
    expect(ROWS.map((row) => passes(row))).toEqual(ROWS.map((row) => hiddenBy(row) === null))
  })

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
    expect(activeTiers.value.size).toBe(0)
    expect(shown(passes)).toEqual([1, 2, 3, 4])
  })

  it('drops the chips when the user switches the view they name buckets of', async () => {
    const state = reactive({
      tiers: [],
      search: '',
      roles: true,
      verdict: 'all',
      engineView: false
    })
    const { toggleTier, activeTiers } = useLedgerFilters({
      bucketOf: (row) => row.bucket,
      state,
      view: () => state.engineView
    })
    toggleTier('candidate')
    state.engineView = true
    await nextTick()
    expect(activeTiers.value.size).toBe(0)
    expect(state.tiers).toEqual([])
  })

  // A run that loads with or without engine tiers changes what the strip
  // offers, not what the reader chose: the chips must survive it.
  it('keeps the chips when only the data the view reads changes', async () => {
    const state = reactive({ tiers: [], search: '', roles: true, verdict: 'all', engineView: true })
    const hasEngineTiers = ref(false)
    const { toggleTier, activeTiers } = useLedgerFilters({
      bucketOf: (row) => row.bucket,
      state,
      view: () => state.engineView && hasEngineTiers.value
    })
    toggleTier('candidate')
    hasEngineTiers.value = true
    await nextTick()
    expect([...activeTiers.value]).toEqual(['candidate'])
  })

  // "not stated" held while the strip has no such chip would empty the table
  // for a reason nothing on screen shows.
  it('filters nothing by a held chip the strip does not offer now', () => {
    const view = ref(false)
    const state = reactive({
      tiers: ['engine_unstated'],
      search: '',
      roles: true,
      verdict: 'all',
      engineView: true
    })
    const { passes, activeTiers } = useLedgerFilters({ bucketOf: (row) => row.bucket, state, view })
    expect(activeTiers.value.size).toBe(0)
    expect(shown(passes)).toEqual([1, 2, 3, 4])
    view.value = true
    expect(shown(passes)).toEqual([])
  })

  it('hides the role rows while roles are off, and their chips with them', () => {
    const state = reactive({
      tiers: ['reagent'],
      search: '',
      roles: true,
      verdict: 'all',
      engineView: false
    })
    const { passes } = useLedgerFilters({ bucketOf: (row) => row.bucket, state })
    expect(shown(passes)).toEqual([4])
    state.roles = false
    expect(shown(passes)).toEqual([1, 2, 3])
  })

  it('searches what the scope reads off a row, within its m/z window', () => {
    const MZ = { 1: 78.0464, 2: 92.0621, 3: 150.1, 4: 61.988 }
    const { searchQuery, passes } = useLedgerFilters({
      bucketOf: (row) => row.bucket,
      search: { texts: (row) => [row.formula], mzs: (row) => [MZ[row.id]] },
      tolerancePpm: 5
    })
    searchQuery.value = 'c7'
    expect(shown(passes)).toEqual([2])
    searchQuery.value = '78.0466'
    expect(shown(passes)).toEqual([1])
    searchQuery.value = '78.0470'
    expect(shown(passes)).toEqual([])
    searchQuery.value = ''
    expect(shown(passes)).toEqual([1, 2, 3, 4])
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
