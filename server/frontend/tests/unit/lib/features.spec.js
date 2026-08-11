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
