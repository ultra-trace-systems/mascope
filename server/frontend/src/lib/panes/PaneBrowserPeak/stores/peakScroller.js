import { ref, computed } from 'vue'
import { defineStore } from 'pinia'

const SORT_STORAGE_KEY = 'mascope.browser.peak.sort'

function loadSortConfig() {
  try {
    const stored = localStorage.getItem(SORT_STORAGE_KEY)
    if (stored) return JSON.parse(stored)
  } catch {}
  return { sortField: 'height', sortOrder: -1 }
}

export const usePeakScroller = defineStore('browser.peak.scroller', () => {
  // Component refs and data getters bound from PaneBrowserPeak
  const tableRef = ref(null)
  const getPeaks = ref(() => [])

  // Persistent sort state
  const initialSort = loadSortConfig()
  const sortField = ref(initialSort.sortField)
  const sortOrder = ref(initialSort.sortOrder)

  /**
   * Get sorted/filtered data matching DataTable's actual display order.
   * Prefers DataTable's internal processedData, falls back to manual sorting.
   */
  const sortedPeaks = computed(() => {
    // Use DataTable's internal sorted data if available
    if (tableRef.value?.processedData) {
      return tableRef.value.processedData
    }

    // Fallback: manually sort using current config
    const peaks = getPeaks.value()
    const { sortField: field, sortOrder: order } = {
      sortField: sortField.value,
      sortOrder: sortOrder.value
    }

    if (!field) return peaks

    return [...peaks].sort((a, b) => {
      // Navigate nested properties (e.g., 'match.match_score')
      const getValue = (obj, path) => path.split('.').reduce((val, key) => val?.[key], obj)
      const aVal = getValue(a, field)
      const bVal = getValue(b, field)

      // Handle null/undefined
      if (aVal == null && bVal == null) return 0
      if (aVal == null) return 1
      if (bVal == null) return -1

      // Apply sort direction
      const dir = order || -1
      return aVal < bVal ? -1 * dir : aVal > bVal ? 1 * dir : 0
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
   * Scroll to peak using VirtualScroller's native scrollInView method.
   * Uses display index from sorted data to match visual position.
   */
  async function scrollToPeak(peakId) {
    if (!scrollLock && peakId && tableRef.value) {
      scrollLock = true

      // Allow DOM to update
      await new Promise((resolve) => setTimeout(resolve, 0))

      // Find peak's index in display order
      const displayIndex = sortedPeaks.value.findIndex((p) => p.peak_id === peakId)
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
          const itemSize = 35.5
          container.scrollTop = displayIndex * itemSize

          setTimeout(() => {
            document.getElementById(peakId)?.scrollIntoView({
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
   * Update sort field and order, persisting to localStorage.
   */
  function setSort(field, order) {
    sortField.value = field
    sortOrder.value = order
    try {
      localStorage.setItem(SORT_STORAGE_KEY, JSON.stringify({ sortField: field, sortOrder: order }))
    } catch {}
  }

  /**
   * Bind component refs and data from PaneBrowserPeak.
   * Pass getter functions to maintain reactivity across component updates.
   */
  function bind(table, peaksGetter) {
    tableRef.value = table
    getPeaks.value = peaksGetter || (() => [])
  }

  return {
    sortField,
    sortOrder,
    setSort,
    bind,
    scrollToPeak
  }
})
