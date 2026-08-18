import { describe, it, expect, vi } from 'vitest'

import { createDetailLoader } from '@/stores/data/modules/peakAssignment/detail'

// A slim ledger row: no `alternatives` / `provenance` keys at all.
const slimRow = (id = 'a1') => ({
  peak_assignment_id: id,
  sample_item_id: 's1',
  sample_peak_id: 'peak-1',
  assigned_formula: 'C6H12O6'
})

const fullRecord = (id = 'a1') => ({
  ...slimRow(id),
  alternatives: [{ assigned_formula: 'C7H16O5' }],
  provenance: { p_correct: 0.93 }
})

describe('peakAssignment detail loader', () => {
  it('fetches the full record for a slim row and caches it', async () => {
    const fetchDetail = vi.fn(async () => fullRecord())
    const loader = createDetailLoader(fetchDetail)

    expect(loader.detailOf('a1')).toBeNull()
    const record = await loader.loadDetail(slimRow())
    expect(record.provenance.p_correct).toBe(0.93)
    // The cache is a reactive Map, so reads come back as reactive proxies:
    // equal in content, not by identity.
    expect(loader.detailOf('a1')).toStrictEqual(record)

    // A second load is served from the cache.
    await loader.loadDetail(slimRow())
    expect(fetchDetail).toHaveBeenCalledTimes(1)
  })

  it('shares one request between concurrent loads of the same assignment', async () => {
    let resolve
    const fetchDetail = vi.fn(() => new Promise((r) => (resolve = r)))
    const loader = createDetailLoader(fetchDetail)

    const first = loader.loadDetail(slimRow())
    const second = loader.loadDetail(slimRow())
    resolve(fullRecord())
    expect(await first).toEqual(await second)
    expect(fetchDetail).toHaveBeenCalledTimes(1)
  })

  it('uses a pre-slim row as-is without fetching', async () => {
    // A backend predating the slim projection serves the detail on the list
    // row itself (even when null); nothing extra should be fetched.
    const fetchDetail = vi.fn()
    const loader = createDetailLoader(fetchDetail)

    const record = await loader.loadDetail({ ...slimRow(), alternatives: null, provenance: null })
    expect(record.peak_assignment_id).toBe('a1')
    expect(loader.detailOf('a1')).toStrictEqual(record)
    expect(fetchDetail).not.toHaveBeenCalled()
  })

  it('does not cache a missing record, and clear() forces a refetch', async () => {
    const fetchDetail = vi.fn().mockResolvedValueOnce(null).mockResolvedValue(fullRecord())
    const loader = createDetailLoader(fetchDetail)

    expect(await loader.loadDetail(slimRow())).toBeNull()
    expect(loader.detailOf('a1')).toBeNull()

    // The miss was not cached: the next load fetches again.
    expect(await loader.loadDetail(slimRow())).toEqual(fullRecord())

    loader.clear()
    expect(loader.detailOf('a1')).toBeNull()
    await loader.loadDetail(slimRow())
    expect(fetchDetail).toHaveBeenCalledTimes(3)
  })

  it('does not cache a failed fetch', async () => {
    const fetchDetail = vi
      .fn()
      .mockRejectedValueOnce(new Error('network'))
      .mockResolvedValue(fullRecord())
    const loader = createDetailLoader(fetchDetail)

    await expect(loader.loadDetail(slimRow())).rejects.toThrow('network')
    expect(loader.detailOf('a1')).toBeNull()
    expect(await loader.loadDetail(slimRow())).toEqual(fullRecord())
  })

  it('ignores rows without an assignment id', async () => {
    const fetchDetail = vi.fn()
    const loader = createDetailLoader(fetchDetail)
    expect(await loader.loadDetail(null)).toBeNull()
    expect(await loader.loadDetail({})).toBeNull()
    expect(fetchDetail).not.toHaveBeenCalled()
  })
})
