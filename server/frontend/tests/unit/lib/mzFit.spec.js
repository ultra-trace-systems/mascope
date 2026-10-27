import { describe, it, expect, vi, beforeEach } from 'vitest'

// The calibration dialog's preview fit is rendered in the dialog, so its
// outcome is claimed and not toasted on top. Applying the fit is not claimed:
// that is news the user waits for in a toast.
const { post, claim } = vi.hoisted(() => ({
  post: vi.fn(),
  claim: vi.fn()
}))

vi.mock('@/api', () => ({ api: { http: { post, get: vi.fn() } } }))
vi.mock('@/stores', () => ({
  useApp: () => ({ ui: { notification: { on: vi.fn(), claim } } })
}))

import { useMzFit } from '@/lib/mzFit'

const SAMPLE = { sample_item_id: 'si-1', filename: 'f-1' }

describe('the m/z fit', () => {
  beforeEach(() => vi.clearAllMocks())

  it('claims the preview fit by the process id the launch was acknowledged as', async () => {
    post.mockResolvedValue({ status: 202, headers: { 'process-id': 'p-fit' } })

    await useMzFit().compute(SAMPLE)

    expect(post).toHaveBeenCalledWith(
      '/calibration/mz_fit',
      expect.any(Object),
      expect.objectContaining({ params: { sample_item_id: 'si-1' } })
    )
    expect(claim).toHaveBeenCalledWith('p-fit')
    // The `process` handler would throw the response, and its header, away.
    expect(post.mock.calls[0][2]).not.toHaveProperty('use')
  })

  it('claims nothing for an acknowledgement that names no process', async () => {
    post.mockResolvedValue({ status: 202, headers: {} })

    await useMzFit().compute(SAMPLE)

    expect(claim).toHaveBeenCalledWith(undefined)
  })

  it('does not claim applying the fit', async () => {
    post.mockResolvedValue({ status: 202, headers: { 'process-id': 'p-apply' } })

    await useMzFit().apply(SAMPLE)

    expect(claim).not.toHaveBeenCalled()
  })
})
