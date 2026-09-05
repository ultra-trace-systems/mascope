import { ref, computed } from 'vue'
import { defineStore } from 'pinia'

export const useIonScroller = defineStore('browser.match.ion.scroller', () => {
  // Component refs and data getters bound from MatchIonTable
  const tableRef = ref(null)
  const getIons = ref(() => [])
  const getSortConfig = ref(() => ({ sortField: 'match.match_score', sortOrder: -1 }))

  /**
   * Get sorted/filtered data matching DataTable's actual display order.
   * Prefers DataTable's internal processedData, falls back to manual sorting.
   */
  const sortedIons = computed(() => {
    // Use DataTable's internal sorted data if available
    if (tableRef.value?.processedData) {
      return tableRef.value.processedData
    }

    // Fallback: manually sort using current config
    const ions = getIons.value()
    const { sortField, sortOrder } = getSortConfig.value()

    if (!sortField) return ions

    return [...ions].sort((a, b) => {
      // Navigate nested properties (e.g., 'match.match_score')
      const getValue = (obj, path) => path.split('.').reduce((val, key) => val?.[key], obj)
      const aVal = getValue(a, sortField)
      const bVal = getValue(b, sortField)

      // Handle null/undefined
      if (aVal == null && bVal == null) return 0
      if (aVal == null) return 1
      if (bVal == null) return -1

      // Apply sort direction
      const order = sortOrder || -1
      return aVal < bVal ? -1 * order : aVal > bVal ? 1 * order : 0
    })
  })

  /**
   * The VirtualScroller instance behind the table, for its native scroll
   * methods. Reached through the DataTable's own accessor, a method of the
   * component instance that a production bundle keeps; the `__vnode` handle on
   * the scroller's element exists only in development builds, so a reach
   * through it takes the fallback everywhere else.
   */
  function getScrollerInstance() {
    return tableRef.value?.getVirtualScrollerRef?.()
  }

  let scrollLock = false

  /**
   * Scroll to ion using VirtualScroller's native scrollInView method.
   * Uses display index from sorted data to match visual position.
   */
  async function scrollToIon(ionId) {
    if (!scrollLock && ionId && tableRef.value) {
      scrollLock = true

      // Allow DOM to update
      await new Promise((resolve) => setTimeout(resolve, 0))

      // Find ion's index in display order
      const displayIndex = sortedIons.value.findIndex((i) => i.target_ion_id === ionId)
      if (displayIndex === -1) {
        scrollLock = false
        return
      }

      const scroller = getScrollerInstance()

      if (scroller?.scrollInView) {
        // Use native VirtualScroller method (minimal scroll)
        scroller.scrollInView(displayIndex)
      } else {
        // Fallback: manual scroll calculation
        const container = tableRef.value.$el.querySelector('.p-virtualscroller')
        if (container) {
          const itemSize = 35.74
          container.scrollTop = displayIndex * itemSize

          setTimeout(() => {
            document.getElementById(ionId)?.scrollIntoView({
              behavior: 'smooth',
              block: 'nearest'
            })
          }, 100)
        }
      }

      // Release lock after animation completes
      setTimeout(() => {
        scrollLock = false
      }, 300)
    }
  }

  /**
   * Bind component refs and data from MatchIonTable.
   * Pass getter functions to maintain reactivity across component updates.
   */
  function bind(table, ionsGetter, sortConfigGetter) {
    tableRef.value = table
    getIons.value = ionsGetter || (() => [])
    getSortConfig.value =
      sortConfigGetter || (() => ({ sortField: 'match.match_score', sortOrder: -1 }))
  }

  return {
    bind,
    scrollToIon
  }
})
