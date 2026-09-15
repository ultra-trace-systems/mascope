import { ref, computed } from 'vue'
import { defineStore } from 'pinia'

export const useSampleScroller = defineStore('browser.sample.scroller', () => {
  // Component refs and data getters bound from SampleTable
  const tableRef = ref(null)
  const getSamples = ref(() => [])
  const getSortConfig = ref(() => ({ sortField: null, sortOrder: 1 }))

  /**
   * Get sorted/filtered data matching DataTable's actual display order.
   * Prefers DataTable's internal processedData, falls back to manual sorting.
   */
  const sortedSamples = computed(() => {
    // Use DataTable's internal sorted data if available
    if (tableRef.value?.processedData) {
      return tableRef.value.processedData
    }

    // Fallback: manually sort using current config
    const samples = getSamples.value()
    const { sortField, sortOrder } = getSortConfig.value()

    if (!sortField) return samples

    return [...samples].sort((a, b) => {
      // Navigate nested properties (e.g., 'match.match_score')
      const getValue = (obj, path) => path.split('.').reduce((val, key) => val?.[key], obj)
      const aVal = getValue(a, sortField)
      const bVal = getValue(b, sortField)

      // Handle null/undefined
      if (aVal == null && bVal == null) return 0
      if (aVal == null) return 1
      if (bVal == null) return -1

      // Apply sort direction
      const order = sortOrder || 1
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
   * Scroll to sample using VirtualScroller's native scrollInView method.
   * Uses display index from sorted data to match visual position.
   */
  async function scrollToSample(sampleId) {
    if (!scrollLock && sampleId && tableRef.value) {
      scrollLock = true

      // Allow DOM to update
      await new Promise((resolve) => setTimeout(resolve, 0))

      // Find sample's index in display order
      const displayIndex = sortedSamples.value.findIndex((s) => s.sample_item_id === sampleId)
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
            document.getElementById(sampleId)?.scrollIntoView({
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
   * Scroll to first selected sample in display order.
   * Used when multiple samples selected (e.g., from batch chart).
   */
  function scrollToSamples(sampleIds) {
    if (!sampleIds?.length) return

    // Find first selected sample in current display order
    const firstSample = sortedSamples.value.find((s) => sampleIds.includes(s.sample_item_id))
    if (firstSample) {
      scrollToSample(firstSample.sample_item_id)
    }
  }

  /**
   * Bind component refs and data from SampleTable.
   * Pass getter functions to maintain reactivity across component updates.
   */
  function bind(table, samplesGetter, sortConfigGetter) {
    tableRef.value = table
    getSamples.value = samplesGetter || (() => [])
    getSortConfig.value = sortConfigGetter || (() => ({ sortField: null, sortOrder: 1 }))
  }

  return {
    bind,
    scrollToSample,
    scrollToSamples
  }
})
