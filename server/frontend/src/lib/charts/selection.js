import { computed } from 'vue'

/**
 * The selected samples as point indices into traces that carry one point per
 * sample of `app.data.sample.list`, for BaseChartPlotly's `selectedPoints`.
 *
 * Matched by id: a reload of the list replaces its records, and the selection
 * store restores the same ids on the new ones. Null when no selected sample is
 * in the list.
 *
 * Every change of the selection is a new array, even one with the same
 * indices: a box select drawn over some traces dims the points on all the
 * others, and the selection the store takes from it has to be drawn back over
 * every trace.
 */
export function useSelectedSamplePoints(app) {
  return computed(() => {
    const selectedIds = new Set(app.data.sample.selectedIds)
    const indices = []
    if (selectedIds.size > 0) {
      app.data.sample.list.forEach((sample, index) => {
        if (selectedIds.has(sample.sample_item_id)) indices.push(index)
      })
    }
    return indices.length > 0 ? indices : null
  })
}
