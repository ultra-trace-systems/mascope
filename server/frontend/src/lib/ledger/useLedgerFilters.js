import { reactive, ref, watch } from 'vue'
import { FilterMatchMode, FilterOperator, FilterService } from '@primevue/core/api'

import { ENGINE_UNSTATED, ENGINE_UNSTATED_META, ROLE_BUCKETS, TIERS, TIER_META } from '@/lib/tiers'

/**
 * The chips of a ledger's tier strip: one per tier in confidence order, then
 * the engine view's "not stated", then one per role, each with its count.
 *
 * @param {Object<string, number>} counts rows per bucket
 * @param {{ engineView?: boolean }} options whether the chips name the
 *   producing engine's buckets
 * @returns {Array<{ key: string, label: string, count: number, role: boolean }>}
 */
export function ledgerTierChips(counts, { engineView = false } = {}) {
  const buckets = [...TIERS, ...(engineView ? [ENGINE_UNSTATED] : []), ...ROLE_BUCKETS]
  const labels = { ...TIER_META, [ENGINE_UNSTATED]: ENGINE_UNSTATED_META }
  return buckets.map((bucket) => ({
    key: bucket,
    label: labels[bucket]?.label ?? bucket,
    count: counts?.[bucket] ?? 0,
    role: ROLE_BUCKETS.includes(bucket)
  }))
}

/**
 * A ledger's filters: the tier chips, the column filter menus and the verdict
 * filter, applied by the scope to its top-level rows (the table is `lazy`, so
 * PrimeVue filters nothing itself).
 *
 * The tier chips are multi-select in both scopes: no chip on is every row, a
 * chip on is its bucket, two on are either. A chip names a bucket of one view
 * (this server's tiers or the producing engine's), so a change of view drops
 * them rather than carrying "candidate" over to a tier it never meant.
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
 * @param {Object<string, string>} [options.menus] column field -> match mode
 * @param {Object<string, Function>} [options.valueOf] what a menu reads, where
 *   that is not the field it is named by
 * @param {Object<string, import('vue').Ref<boolean>>} [options.menuShown]
 *   whether a menu's column is on screen
 * @param {import('vue').WatchSource} [options.view] the strip's view; a change
 *   drops the chips
 * @param {Function} [options.verdictOf] the verdict a row visibly carries, for
 *   the verdict filter
 */
export function useLedgerFilters({
  bucketOf,
  menus = {},
  valueOf = {},
  menuShown = {},
  view = null,
  verdictOf = null
}) {
  const activeTiers = reactive(new Set())
  function toggleTier(key) {
    if (activeTiers.has(key)) activeTiers.delete(key)
    else activeTiers.add(key)
  }
  if (view) watch(view, () => activeTiers.clear())

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
  const verdictFilter = ref('all')

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
    if (activeTiers.size && !activeTiers.has(bucketOf(row))) return false
    if (!passesMenus(row)) return false
    if (verdictOf && verdicts && verdictFilter.value !== 'all') {
      return (verdictOf(row)?.verdict ?? 'unverified') === verdictFilter.value
    }
    return true
  }

  return { activeTiers, toggleTier, filters, verdictFilter, passes }
}
