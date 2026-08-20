import { describe, it, expect, vi } from 'vitest'
import { ref } from 'vue'

import { useLoader } from '@/lib/store/loader'

const silentLogger = {
  log: vi.fn(),
  debug: vi.fn(),
  info: vi.fn(),
  warn: vi.fn(),
  error: vi.fn()
}

const makeLoader = (method) => {
  const records = ref([])
  const pending = ref(false)
  const loader = useLoader(
    'test',
    'id',
    method,
    { records, pending, selection: null, detailed: null },
    {},
    silentLogger
  )
  return { loader, records, pending }
}

describe('useLoader sync', () => {
  it('clears pending after a successful load', async () => {
    const { loader, records, pending } = makeLoader(async () => [{ id: 'a' }])

    await loader.sync({ context: 'test' })

    expect(pending.value).toBe(false)
    expect(records.value).toHaveLength(1)
  })

  it('clears pending when the fetch rejects, so the pane cannot latch its spinner', async () => {
    // A rejected fetch used to abort sync before pending was cleared, leaving
    // every consuming pane rendering a spinner for the rest of the session.
    const { loader, pending } = makeLoader(async () => {
      throw new Error('request failed')
    })

    await expect(loader.sync({ context: 'test' })).rejects.toThrow('request failed')

    expect(pending.value).toBe(false)
  })

  it('keeps the previous records when the fetch rejects', async () => {
    // Stale rows are truthful; an empty list would read as "this sample has none".
    let fail = false
    const { loader, records } = makeLoader(async () => {
      if (fail) throw new Error('request failed')
      return [{ id: 'a' }, { id: 'b' }]
    })

    await loader.sync({ context: 'test' })
    fail = true
    await expect(loader.sync({ context: 'test' })).rejects.toThrow('request failed')

    expect(records.value).toHaveLength(2)
  })
})
