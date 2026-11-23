import { describe, it, expect, vi } from 'vitest'
import { nextTick } from 'vue'

import { useRowPopover } from '@/lib/ledger/useRowPopover.js'

// One popover for a whole table: the open row's cell toggles it, another row's
// cell moves it there, and the opener's hook runs before either.

const ROWS = [{ id: 'a' }, { id: 'b' }]

function setup({ beforeOpen } = {}) {
  const calls = []
  const popover = {
    toggle: vi.fn(() => calls.push('toggle')),
    hide: vi.fn(() => calls.push('hide')),
    show: vi.fn(() => calls.push('show'))
  }
  const hook = beforeOpen ? vi.fn(() => calls.push('beforeOpen')) : undefined
  const rows = useRowPopover(
    (id) => ROWS.find((row) => row.id === id),
    (row) => row.id,
    { beforeOpen: hook }
  )
  rows.menu.value = popover
  return { rows, popover, calls, hook }
}

describe('useRowPopover', () => {
  it('opens at the row whose cell was clicked, and points its target there', async () => {
    const { rows, popover } = setup()
    const event = new Event('click')

    rows.open(event, ROWS[0])
    expect(popover.hide).toHaveBeenCalledTimes(1)
    expect(popover.show).not.toHaveBeenCalled()
    await nextTick()
    expect(popover.show).toHaveBeenCalledWith(event)
    expect(rows.target.value).toBe(ROWS[0])
  })

  it("toggles on the open row's cell rather than opening it again", async () => {
    const { rows, popover } = setup()
    rows.open(new Event('click'), ROWS[0])
    await nextTick()

    const again = new Event('click')
    rows.open(again, ROWS[0])
    await nextTick()
    expect(popover.toggle).toHaveBeenCalledWith(again)
    expect(popover.hide).toHaveBeenCalledTimes(1)
    expect(popover.show).toHaveBeenCalledTimes(1)
  })

  it("moves to another row's cell: hides, then shows there", async () => {
    const { rows, popover, calls } = setup()
    rows.open(new Event('click'), ROWS[0])
    await nextTick()

    const other = new Event('click')
    rows.open(other, ROWS[1])
    await nextTick()
    expect(calls).toEqual(['hide', 'show', 'hide', 'show'])
    expect(popover.show).toHaveBeenLastCalledWith(other)
    expect(popover.toggle).not.toHaveBeenCalled()
    expect(rows.target.value).toBe(ROWS[1])
  })

  it('runs the opener hook before toggling or moving', async () => {
    const { rows, calls } = setup({ beforeOpen: true })
    rows.open(new Event('click'), ROWS[0])
    await nextTick()
    rows.open(new Event('click'), ROWS[0])
    rows.open(new Event('click'), ROWS[1])
    await nextTick()
    expect(calls).toEqual([
      'beforeOpen',
      'hide',
      'show',
      'beforeOpen',
      'toggle',
      'beforeOpen',
      'hide',
      'show'
    ])
  })

  it('reads its target live, and has none once the row is gone', () => {
    const rows = useRowPopover(
      (id) => (id === 'a' ? { id: 'a', fresh: true } : undefined),
      (row) => row.id
    )
    rows.menu.value = { toggle() {}, hide() {}, show() {} }
    expect(rows.target.value).toBeNull()
    rows.open(new Event('click'), { id: 'a' })
    expect(rows.target.value).toEqual({ id: 'a', fresh: true })
    rows.rowId.value = 'gone'
    expect(rows.target.value).toBeNull()
  })

  it('does nothing before the popover is mounted', () => {
    const hook = vi.fn()
    const rows = useRowPopover(
      (id) => ROWS.find((row) => row.id === id),
      (row) => row.id,
      { beforeOpen: hook }
    )
    rows.open(new Event('click'), ROWS[0])
    expect(hook).not.toHaveBeenCalled()
    expect(rows.rowId.value).toBeNull()
    expect(() => rows.hide()).not.toThrow()
  })
})
