import { describe, it, expect, vi, afterEach } from 'vitest'
import { mount, flushPromises, enableAutoUnmount } from '@vue/test-utils'

import BaseClipboardContext from '@/lib/base/BaseClipboardContext.vue'

// The paste area of the batch import and target collection dialogs. What was
// pasted comes with the paste event, which works in any context; the async
// Clipboard API's readText is missing on a page served over plain HTTP from a
// network address, and may be refused or prompt for permission anywhere, so it
// is not used at all. A paste that cannot be read has to say so rather than
// fail with a TypeError that only reaches the console.

const mountContext = (props = {}) =>
  mount(BaseClipboardContext, {
    props: { persistMessage: true, ...props },
    global: {
      stubs: { Message: { template: '<div role="status"><slot /></div>' } }
    }
  })

// A paste event, carrying `text` the way a browser's does - readable only while
// the event is being dispatched, as a DataTransfer is - or no clipboard data at
// all, as an event raised by script does.
const paste = async (wrapper, text) => {
  const event = new Event('paste', { bubbles: true })
  let dispatching = true
  if (text !== undefined) {
    Object.defineProperty(event, 'clipboardData', {
      value: { getData: (type) => (dispatching && type === 'text/plain' ? text : '') }
    })
  }
  wrapper.element.dispatchEvent(event)
  dispatching = false
  await flushPromises()
}

enableAutoUnmount(afterEach)

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('BaseClipboardContext', () => {
  it('takes the pasted text from the paste event where there is no Clipboard API', async () => {
    vi.stubGlobal('navigator', {})
    const parse = vi.fn((text) => text.split('\t'))
    const wrapper = mountContext({ parse })

    await paste(wrapper, 'C6H6\tbenzene')

    expect(parse).toHaveBeenCalledWith('C6H6\tbenzene')
    expect(wrapper.emitted('validated')[0][0].data).toEqual(['C6H6', 'benzene'])
  })

  it('never reads the clipboard itself', async () => {
    const readText = vi.fn().mockResolvedValue('stale')
    vi.stubGlobal('navigator', { clipboard: { readText } })
    const parse = vi.fn((text) => text)
    const wrapper = mountContext({ parse })

    await paste(wrapper, 'C6H6')
    await paste(wrapper)

    expect(parse).toHaveBeenCalledTimes(1)
    expect(parse).toHaveBeenCalledWith('C6H6')
    expect(readText).not.toHaveBeenCalled()
  })

  it('says so when the paste carries nothing it can read', async () => {
    vi.stubGlobal('navigator', {})
    const parse = vi.fn()
    const wrapper = mountContext({ parse })

    await paste(wrapper)

    expect(parse).not.toHaveBeenCalled()
    expect(wrapper.emitted('validated')).toBeUndefined()
    expect(wrapper.text()).toContain('Could not read what was pasted')
  })
})
