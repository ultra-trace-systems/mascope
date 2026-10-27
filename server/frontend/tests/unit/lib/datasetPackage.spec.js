import { describe, it, expect, vi, beforeEach } from 'vitest'

// The deposit package's requests go to the dataset's own workspace, and its
// labels say what the package holds in words a user reads.

vi.mock('@/api', () => ({ api: { http: { get: vi.fn(), post: vi.fn() } } }))

import { api } from '@/api'
import {
  batchesLabel,
  collectionLabel,
  exportPackage,
  gapEditable,
  loadPackagePreview,
  notKeptLabel,
  rawFilesLabel,
  sizeLabel
} from '@/lib/datasetPackage'

const DATASET = { dataset_id: 'ds-1', workspace_id: 'ws-1', dataset_name: 'Winter run' }

beforeEach(() => {
  api.http.get.mockReset()
  api.http.post.mockReset()
})

describe('datasetPackage requests', () => {
  it('reads the preview of the dataset in its workspace, errors shown in place', async () => {
    api.http.get.mockResolvedValue({ data: { data: { archive: 'a.zip' } } })

    expect(await loadPackagePreview(DATASET)).toEqual({ archive: 'a.zip' })
    expect(api.http.get).toHaveBeenCalledWith(
      '/workspaces/ws-1/datasets/ds-1/export/package/preview',
      expect.objectContaining({ errors: 'inline' })
    )
  })

  it('builds the package as a process, whose notification brings the download', async () => {
    api.http.post.mockResolvedValue({})

    await exportPackage(DATASET)
    expect(api.http.post).toHaveBeenCalledWith(
      '/workspaces/ws-1/datasets/ds-1/export/package',
      {},
      { use: 'process', type: 'export_dataset_package' }
    )
  })
})

describe('datasetPackage labels', () => {
  it.each([
    [0, '0 B'],
    [999, '999 B'],
    [1000, '1.0 kB'],
    [27_900, '27.9 kB'],
    [3_400_000, '3.4 MB'],
    [1_200_000_000, '1.2 GB'],
    [5_000_000_000_000, '5000.0 GB']
  ])('gives %i bytes as %s, as the server does', (bytes, label) => {
    expect(sizeLabel(bytes)).toBe(label)
  })

  it('counts the batches and their samples together', () => {
    expect(batchesLabel({ batches: [{ samples: 2 }, { samples: 1 }] })).toBe('2 batches, 3 samples')
    expect(batchesLabel({ batches: [{ samples: 1 }] })).toBe('1 batch, 1 sample')
    expect(batchesLabel({ batches: [] })).toBe('0 batches, 0 samples')
  })

  it('says how many raw files are packed of those there are, and their size', () => {
    expect(rawFilesLabel({ raw_files: 3, raw_files_included: 3, raw_bytes: 2500 })).toBe(
      '3 files, 2.5 kB'
    )
    expect(rawFilesLabel({ raw_files: 3, raw_files_included: 2, raw_bytes: 2500 })).toBe(
      '2 of 3 files, 2.5 kB'
    )
  })

  it('names the first files not kept and counts the rest', () => {
    expect(notKeptLabel(['a'])).toBe('a')
    expect(notKeptLabel(['a', 'b', 'c'])).toBe('a, b, c')
    expect(notKeptLabel(['a', 'b', 'c', 'd', 'e'])).toBe('a, b, c and 2 more')
  })

  it('names a collection with what it was used as', () => {
    expect(
      collectionLabel({ target_collection_name: 'Targets', used_as: ['matched', 'diagnostic'] })
    ).toBe('Targets (matched, diagnostic)')
  })

  it('sends the metadata gaps to the dataset dialog, not a dataset of no samples', () => {
    expect(gapEditable({ field: 'license' })).toBe(true)
    expect(gapEditable({ field: 'authors' })).toBe(true)
    expect(gapEditable({ field: 'dataset_description' })).toBe(true)
    expect(gapEditable({ field: 'samples' })).toBe(false)
  })
})
