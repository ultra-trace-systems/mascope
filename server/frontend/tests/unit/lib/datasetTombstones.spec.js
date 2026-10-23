import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/api', () => ({ api: { http: { get: vi.fn() } } }))

import { api } from '@/api'
import {
  TOMBSTONE_PAGE_SIZE,
  countsLabel,
  deletedLabel,
  deletedViaLabel,
  loadTombstones,
  metadataLines,
  whenLabel
} from '@/lib/datasetTombstones'

describe('loadTombstones', () => {
  beforeEach(() => api.http.get.mockReset())

  it('reads one page of the route, failures inline', async () => {
    api.http.get.mockResolvedValue({ data: { data: [{ dataset_id: 'a' }], total: 31 } })

    const answer = await loadTombstones(1)

    expect(answer).toEqual({ data: [{ dataset_id: 'a' }], total: 31 })
    expect(api.http.get).toHaveBeenCalledWith('/datasets/tombstones', {
      params: { page: 1, limit: TOMBSTONE_PAGE_SIZE },
      type: 'dataset_tombstones',
      errors: 'inline'
    })
  })

  it('reads an empty answer as no rows', async () => {
    api.http.get.mockResolvedValue({ data: {} })
    expect(await loadTombstones(0, 5)).toEqual({ data: [], total: 0 })
  })
})

describe('labels', () => {
  it('says whether the dataset went with its workspace', () => {
    expect(deletedViaLabel({ deleted_via: 'workspace' })).toBe('with its workspace')
    expect(deletedViaLabel({ deleted_via: 'dataset' })).toBe('')
  })

  it('says when and by whom, and with its workspace only when it was', () => {
    const stamp = '2026-10-20T08:30:00+00:00'
    const when = new Date(stamp).toLocaleString()
    expect(
      deletedLabel({ deleted_utc: stamp, deleted_by: 'a@example.org', deleted_via: 'dataset' })
    ).toBe(`${when} by a@example.org`)
    expect(
      deletedLabel({ deleted_utc: stamp, deleted_by: 'a@example.org', deleted_via: 'workspace' })
    ).toBe(`${when} by a@example.org with its workspace`)
    expect(deletedLabel({ deleted_utc: stamp, deleted_by: null, deleted_via: 'dataset' })).toBe(
      when
    )
  })

  it('counts batches, samples and files, singular for one', () => {
    expect(countsLabel({ n_sample_batches: 1, n_sample_items: 1200, n_sample_files: 0 })).toBe(
      '1 batch, 1,200 samples, 0 files'
    )
    expect(countsLabel({ n_sample_batches: 2, n_sample_items: 1, n_sample_files: 1 })).toBe(
      '2 batches, 1 sample, 1 file'
    )
    expect(countsLabel({ n_sample_batches: 0, n_sample_items: 0, n_sample_files: 3 })).toBe(
      '0 batches, 0 samples, 3 files'
    )
    // A batch with no samples yet still held something.
    expect(countsLabel({ n_sample_batches: 1, n_sample_items: 0, n_sample_files: 0 })).toBe(
      '1 batch, 0 samples, 0 files'
    )
    expect(countsLabel({ n_sample_batches: 0, n_sample_items: 0, n_sample_files: 0 })).toBe('-')
    expect(countsLabel({})).toBe('-')
  })

  it('writes a time in the locale, a dash for none or nonsense', () => {
    const stamp = '2026-10-20T08:30:00+00:00'
    expect(whenLabel(stamp)).toBe(new Date(stamp).toLocaleString())
    expect(whenLabel(null)).toBe('-')
    expect(whenLabel('not a time')).toBe('-')
  })
})

describe('metadataLines', () => {
  it('lists every described field in the dialog order', () => {
    expect(
      metadataLines({
        license: 'CC-BY-4.0',
        authors: [
          { name: 'A. Author', orcid: '0000-0002-1825-0097', affiliation: 'Lab X' },
          { name: 'B. Author', orcid: null, affiliation: null }
        ],
        keywords: ['aerosol', 'winter'],
        related_identifiers: [
          { identifier: '10.1234/x', relation: 'IsSupplementTo', identifier_type: 'DOI' },
          { identifier: 'https://example.org', relation: 'References', identifier_type: null }
        ],
        funding: [
          { funder: 'Funder X', award: 'G-1' },
          { funder: 'Funder Y', award: null }
        ]
      })
    ).toEqual([
      { label: 'Licence', value: 'CC-BY-4.0' },
      { label: 'Author', value: 'A. Author, ORCID 0000-0002-1825-0097, Lab X' },
      { label: 'Author', value: 'B. Author' },
      { label: 'Keywords', value: 'aerosol, winter' },
      { label: 'Related', value: 'IsSupplementTo, DOI 10.1234/x' },
      { label: 'Related', value: 'References, https://example.org' },
      { label: 'Funding', value: 'Funder X, G-1' },
      { label: 'Funding', value: 'Funder Y' }
    ])
  })

  it('yields nothing for a dataset nobody described', () => {
    expect(metadataLines(null)).toEqual([])
    expect(
      metadataLines({
        license: null,
        authors: null,
        keywords: [],
        related_identifiers: null,
        funding: null
      })
    ).toEqual([])
  })
})
