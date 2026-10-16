import { computed } from 'vue'

/**
 * The selected samples as point indices into traces that carry one point per
 * sample of `app.data.sample.list`, for BaseChartPlotly's `selectedPoints`.
 *
 * Matched by id: a reload of the list replaces its records, and the selection
 * store restores the same ids on the new ones. Null when no selected sample is
 * in the list. While the indices stay the same the previous array is kept, so a
 * selection reassigned with the same samples redraws nothing.
 */
export function useSelectedSamplePoints(app) {
  return computed((previous) => {
    const selectedIds = new Set(app.data.sample.selectedIds)
    const next = []
    if (selectedIds.size > 0) {
      app.data.sample.list.forEach((sample, index) => {
        if (selectedIds.has(sample.sample_item_id)) next.push(index)
      })
    }
    if (next.length === 0) return null
    const same = previous?.length === next.length && next.every((index, i) => index === previous[i])
    return same ? previous : next
  })
}
