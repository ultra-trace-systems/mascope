import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { useLedgerLensStore } from '@/lib/ledger/useLedgerLens.js'
import { useLedgerViewStore } from '@/lib/ledger/useLedgerView.js'

// The lens store keeps the scope's sort as the first lens found it, and
// closing the lens puts it back (docs/user/how-it-works/peak-assignment.md,
// "Seeing a group").
describe('useLedgerLensStore: the sort under a lens', () => {
  const GROUP = { group_id: 'g-1', kind: 'channels', members: [] }
  const OTHER = { group_id: 'g-2', kind: 'channels', members: [] }
  let lenses
  let view
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    lenses = useLedgerLensStore()
    view = useLedgerViewStore()
    view.batch.field = 'max_intensity'
    view.batch.order = -1
  })
  const sortOf = () => [view.batch.field, view.batch.order]
  const sortUnder = (field, order) => {
    view.batch.field = field
    view.batch.order = order
    lenses.sortedUnder('batch')
  }

  it('gives back the sort the ledger had when it opened', () => {
    lenses.open('batch', GROUP, { batchId: 'b-1' })
    sortUnder('mz', 1)
    lenses.close('batch')
    expect(lenses.lenses.batch).toBeNull()
    expect(sortOf()).toEqual(['max_intensity', -1])
  })

  it('keeps a cleared sort cleared', () => {
    view.batch.field = null
    lenses.open('batch', GROUP, { batchId: 'b-1' })
    sortUnder('mz', 1)
    lenses.close('batch')
    expect(sortOf()).toEqual([null, -1])
  })

  it('keeps, over a second lens on the same ledger, the sort the first one found', () => {
    lenses.open('batch', GROUP, { batchId: 'b-1' })
    sortUnder('mz', 1)
    lenses.open('batch', OTHER, { batchId: 'b-1' })
    lenses.close('batch')
    expect(sortOf()).toEqual(['max_intensity', -1])
  })

  it('does not carry the sort of a lens left on another batch, which the reader may have sorted since', () => {
    lenses.open('batch', GROUP, { batchId: 'b-1' })
    // The reader moves to another batch, where the first lens is not drawn,
    // and sorts its ledger before opening a lens there.
    view.batch.field = 'mz'
    view.batch.order = 1
    lenses.open('batch', OTHER, { batchId: 'b-2' })
    sortUnder('consensus_formula', 1)
    lenses.close('batch')
    expect(sortOf()).toEqual(['mz', 1])
  })

  it("leaves the other scope's sort alone", () => {
    view.sample.field = 'sample_peak_mz'
    view.sample.order = 1
    lenses.open('batch', GROUP, { batchId: 'b-1' })
    sortUnder('mz', 1)
    lenses.close('batch')
    expect([view.sample.field, view.sample.order]).toEqual(['sample_peak_mz', 1])
  })
})
