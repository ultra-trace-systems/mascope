import { computed, reactive, ref, toValue, watch } from 'vue'
import { FilterMatchMode, FilterOperator, FilterService } from '@primevue/core/api'

import { ENGINE_UNSTATED, ENGINE_UNSTATED_META, ROLE_BUCKETS, TIERS, TIER_META } from '@/lib/tiers'

import { BATCH_SEARCH_PPM, matchesLedgerSearch, parseLedgerSearch } from './search.js'

/**
 * The buckets a tier strip has chips for: the tiers in confidence order, then
 * the engine view's "not stated", then the roles while their rows are shown.
 *
 * @param {{ engineView?: boolean, roles?: boolean }} options
 * @returns {string[]}
 */
export function ledgerBuckets({ engineView = false, roles = true } = {}) {
  return [...TIERS, ...(engineView ? [ENGINE_UNSTATED] : []), ...(roles ? ROLE_BUCKETS : [])]
}

/**
 * The chips of a ledger's tier strip: one per bucket of `ledgerBuckets`, each
 * with its count. A chip with nothing in it is left out, unless it is on: an
 * active chip stays, so the filter it holds can be seen and turned off.
 *
 * @param {Object<string, number>} counts rows per bucket
 * @param {{ engineView?: boolean, roles?: boolean, active?: Set<string> }} options
 *   whether the chips name the producing engine's buckets, whether the role
 *   rows are shown, and the chips that are on
 * @returns {Array<{ key: string, label: string, count: number, role: boolean }>}
 */
export function ledgerTierChips(counts, { engineView = false, roles = true, active = null } = {}) {
  const labels = { ...TIER_META, [ENGINE_UNSTATED]: ENGINE_UNSTATED_META }
  return ledgerBuckets({ engineView, roles })
    .map((bucket) => ({
      key: bucket,
      label: labels[bucket]?.label ?? bucket,
      count: counts?.[bucket] ?? 0,
      role: ROLE_BUCKETS.includes(bucket)
    }))
    .filter((chip) => chip.count > 0 || active?.has(chip.key))
}

/**
 * A ledger's filters: the search box, the tier chips, the role rows, the
 * column filter menus and the verdict filter, applied by the scope to its
 * top-level rows (the table is `lazy`, so PrimeVue filters nothing itself).
 *
 * The search, the chips, the role rows and the verdict filter are the scope's
 * choices, held in its ledger view (./useLedgerView.js) when the scope hands
 * that over as `state`, so they persist; without one they are held here.
 *
 * The tier chips are multi-select in both scopes: no chip on is every row, a
 * chip on is its bucket, two on are either. A chip names a bucket of one view
 * (this server's tiers or the producing engine's), so switching the view drops
 * them rather than carrying "candidate" over to a tier it never meant. A chip
 * held for a bucket the strip does not offer now - "not stated" on a run with
 * no engine tiers, a role while the role rows are hidden - filters nothing.
 *
 * A column's menu filters through PrimeVue's own FilterService, with the
 * rules PrimeVue applied itself: a constraint whose value is null is not a
 * filter, and the operator joins the rest. A menu whose column is absent is no
 * filter either - left on, it would empty the table for a reason nothing on
 * screen shows - and it is dropped when the column goes.
 *
 * @param {Object} options
 * @param {Function} options.bucketOf the bucket a row's chip is, in the view
 *   the strip is in
 * @param {Object} [options.state] the scope's ledger view: `tiers`, `search`,
 *   `roles`, `verdict` and `engineView`
 * @param {Object<string, string>} [options.menus] column field -> match mode
 * @param {Object<string, Function>} [options.valueOf] what a menu reads, where
 *   that is not the field it is named by
 * @param {Object<string, import('vue').Ref<boolean>>} [options.menuShown]
 *   whether a menu's column is on screen
 * @param {import('vue').MaybeRefOrGetter<boolean>} [options.view] whether the
 *   strip names the producing engine's buckets
 * @param {Function} [options.verdictOf] the verdict a row visibly carries, for
 *   the verdict filter
 * @param {{ texts: Function, mzs: Function }} [options.search] what the search
 *   box reads off a row: its formula and listed names, and its m/z values
 * @param {import('vue').MaybeRefOrGetter<number|Object>} [options.tolerancePpm]
 *   the search's m/z window (search.js, `matchesLedgerSearch`)
 */
export function useLedgerFilters({
  bucketOf,
  state = null,
  menus = {},
  valueOf = {},
  menuShown = {},
  view = false,
  verdictOf = null,
  search = null,
  tolerancePpm = BATCH_SEARCH_PPM
}) {
  const choices =
    state ?? reactive({ tiers: [], search: '', roles: true, verdict: 'all', engineView: false })

  // The buckets the strip offers now, and the chips on among them.
  const offered = computed(
    () => new Set(ledgerBuckets({ engineView: toValue(view), roles: choices.roles }))
  )
  const activeTiers = computed(() => new Set(choices.tiers.filter((key) => offered.value.has(key))))
  function toggleTier(key) {
    const at = choices.tiers.indexOf(key)
    if (at >= 0) choices.tiers.splice(at, 1)
    else choices.tiers.push(key)
  }
  // The user's switch between the two views, not a run that loads with or
  // without engine tiers: the chips a reader set must survive the remount.
  watch(
    () => choices.engineView,
    () => choices.tiers.splice(0)
  )

  const filters = ref(
    Object.fromEntries(
      Object.entries(menus).map(([field, matchMode]) => [
        field,
        { operator: FilterOperator.AND, constraints: [{ value: null, matchMode }] }
      ])
    )
  )
  for (const [field, shown] of Object.entries(menuShown)) {
    watch(shown, (has) => {
      if (!has) filters.value[field].constraints[0].value = null
    })
  }

  // Single-select; "unverified" is no current verdict.
  const verdictFilter = computed({
    get: () => choices.verdict,
    set: (value) => (choices.verdict = value)
  })
  const searchQuery = computed({
    get: () => choices.search,
    set: (value) => (choices.search = value ?? '')
  })
  const parsedSearch = computed(() => parseLedgerSearch(choices.search))
  const roleBuckets = new Set(ROLE_BUCKETS)

  const read = (field) => valueOf[field] ?? ((row) => row[field])

  function passesMenus(row) {
    for (const [field, meta] of Object.entries(filters.value)) {
      if (menuShown[field] && !menuShown[field].value) continue
      const constraints = (meta.constraints ?? [meta]).filter(
        (constraint) => constraint.value !== null
      )
      if (!constraints.length) continue
      const value = read(field)(row)
      const matches = (constraint) =>
        FilterService.filters[constraint.matchMode ?? FilterMatchMode.STARTS_WITH](
          value,
          constraint.value
        )
      const passed =
        meta.operator === FilterOperator.OR ? constraints.some(matches) : constraints.every(matches)
      if (!passed) return false
    }
    return true
  }

  /**
   * Whether a top-level row passes every filter on.
   *
   * @param {Object} row
   * @param {{ verdicts?: boolean }} [options] `verdicts: false` leaves the
   *   verdict filter out (the sample scope's verification queue)
   */
  function passes(row, { verdicts = true } = {}) {
    const bucket = bucketOf(row)
    if (!choices.roles && roleBuckets.has(bucket)) return false
    if (activeTiers.value.size && !activeTiers.value.has(bucket)) return false
    if (
      search &&
      parsedSearch.value.kind !== 'none' &&
      !matchesLedgerSearch(
        parsedSearch.value,
        { texts: search.texts(row), mzs: search.mzs(row) },
        toValue(tolerancePpm)
      )
    ) {
      return false
    }
    if (!passesMenus(row)) return false
    if (verdictOf && verdicts && verdictFilter.value !== 'all') {
      return (verdictOf(row)?.verdict ?? 'unverified') === verdictFilter.value
    }
    return true
  }

  return { activeTiers, toggleTier, filters, verdictFilter, searchQuery, passes }
}
