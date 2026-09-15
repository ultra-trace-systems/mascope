import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

// The flag is read once at module load from the serialized runtime, so each case
// re-imports the module with a fresh mock of '@/lib/runtime'.
async function loadFeatures(meta) {
  vi.resetModules()
  vi.doMock('@/lib/runtime', () => ({ runtime: meta === undefined ? {} : { meta } }))
  return import('@/lib/features')
}

describe('peakAssignmentEnabled', () => {
  beforeEach(() => vi.resetModules())
  afterEach(() => vi.doUnmock('@/lib/runtime'))

  it('is off when the runtime does not set the flag', async () => {
    const { peakAssignmentEnabled } = await loadFeatures({ api_port: 8090 })
    expect(peakAssignmentEnabled).toBe(false)
  })

  it('is off when there is no meta at all', async () => {
    const { peakAssignmentEnabled } = await loadFeatures(undefined)
    expect(peakAssignmentEnabled).toBe(false)
  })

  it('is off when the flag is explicitly false', async () => {
    const { peakAssignmentEnabled } = await loadFeatures({ peak_assignment: false })
    expect(peakAssignmentEnabled).toBe(false)
  })

  it('is on when the deployment opts in', async () => {
    const { peakAssignmentEnabled } = await loadFeatures({ peak_assignment: true })
    expect(peakAssignmentEnabled).toBe(true)
  })
})

describe('maxUploadBytes', () => {
  const GB = 1024 ** 3

  beforeEach(() => vi.resetModules())
  afterEach(() => vi.doUnmock('@/lib/runtime'))

  it('is the 5 GB default when the runtime does not set the cap', async () => {
    const { maxUploadBytes } = await loadFeatures({ api_port: 8090 })
    expect(maxUploadBytes).toBe(5 * GB)
  })

  it('is the 5 GB default when there is no meta at all', async () => {
    const { maxUploadBytes } = await loadFeatures(undefined)
    expect(maxUploadBytes).toBe(5 * GB)
  })

  it('follows the cap the deployment configures', async () => {
    const { maxUploadBytes } = await loadFeatures({ tus_max_upload_gb: 20 })
    expect(maxUploadBytes).toBe(20 * GB)
  })

  it('falls back to the default for a value that is not a number', async () => {
    const { maxUploadBytes } = await loadFeatures({ tus_max_upload_gb: 'lots' })
    expect(maxUploadBytes).toBe(5 * GB)
  })
})
