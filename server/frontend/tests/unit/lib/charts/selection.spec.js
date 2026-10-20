import { describe, it, expect } from 'vitest'
import { reactive } from 'vue'

import { useSelectedSamplePoints } from '@/lib/charts/selection'

/** The slice of the app store the selection reads: the batch's samples. */
function appWith(ids, selectedIds) {
  return reactive({ data: { sample: { list: records(ids), selectedIds } } })
}

/** Fresh sample records, as every load of the list makes them. */
const records = (ids) => ids.map((id) => ({ sample_item_id: id }))

describe('useSelectedSamplePoints', () => {
  it('puts each selected sample at its place in the list', () => {
    const app = appWith(['s1', 's2', 's3', 's4'], ['s3', 's1'])

    expect(useSelectedSamplePoints(app).value).toEqual([0, 2])
  })

  it('is null without a selection', () => {
    expect(useSelectedSamplePoints(appWith(['s1'], [])).value).toBeNull()
  })

  it('is null when no selected sample is in the list', () => {
    expect(useSelectedSamplePoints(appWith(['s1'], ['gone'])).value).toBeNull()
  })

  // A reload replaces every record, and a sample added since moves the rest.
  it('follows the selected samples through a reload by id', () => {
    const app = appWith(['s1', 's2', 's3'], ['s2'])
    const points = useSelectedSamplePoints(app)
    expect(points.value).toEqual([1])

    app.data.sample.list = records(['s0', 's1', 's2', 's3'])
    expect(points.value).toEqual([2])
  })

  // A box select over some traces dims the others, so the selection the store
  // takes from it is drawn again even when it selects the same samples.
  it('is a new array for a selection given anew with the same samples', () => {
    const app = appWith(['s1', 's2', 's3'], ['s2'])
    const points = useSelectedSamplePoints(app)
    const before = points.value

    app.data.sample.selectedIds = ['s2']
    expect(points.value).toEqual(before)
    expect(points.value).not.toBe(before)
  })
})
