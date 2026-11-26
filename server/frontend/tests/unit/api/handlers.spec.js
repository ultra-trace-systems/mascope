import { describe, it, expect, vi, beforeEach } from 'vitest'

import { toastFor } from '@/lib/toastTitles'

// A hand assignment's toast is the server's message (curate_assignment), and
// it grows with what the edit did: the lines it linked, claimed or left. The
// http layer's `update` handler hands that message on to the notification it
// pushes, and the toast shows it as its detail, whole: nothing on the way cuts
// it.

const push = vi.fn()
vi.mock('@/stores', () => ({ useApp: () => ({ ui: { notification: { push } } }) }))

const { default: handlers } = await import('@/api/handlers.js')

// A message as long as a hand M0's with several lines found and held.
const MESSAGE =
  'C6H11Br2O5 committed by hand at m/z 438.8720. ' +
  Array.from(
    { length: 12 },
    (_, i) =>
      `81Br line found at m/z ${(440.87 + i).toFixed(2)}, not claimed: it reads C${i + 4}H8O2.`
  ).join(' ')

const response = (body) => ({
  status: 200,
  data: body,
  request: { method: 'patch', url: '/peak-assignments/sample/si-1/assignment/pa-1' },
  config: { headers: { 'X-Type': 'curate_assignment' } }
})

describe('the http layer on an update', () => {
  beforeEach(() => push.mockClear())

  it("toasts the server's whole message", () => {
    expect(MESSAGE.length).toBeGreaterThan(600)
    handlers.update(response({ message: MESSAGE, results: 1, data: [] }))

    expect(push).toHaveBeenCalledTimes(1)
    const notification = push.mock.calls[0][0]
    expect(notification).toEqual({ type: 'curate_assignment', message: MESSAGE, status: 'success' })
    expect(toastFor(notification).detail).toBe(MESSAGE)
  })

  it('reads a message nested under data as well', () => {
    handlers.update(response({ data: { message: MESSAGE } }))
    expect(push.mock.calls[0][0].message).toBe(MESSAGE)
  })
})
