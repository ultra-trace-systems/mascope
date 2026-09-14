import { describe, it, expect, vi, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

import BaseClipboardContext from '@/lib/base/BaseClipboardContext.vue'

// The paste area of the batch import and target collection dialogs. What was
// pasted comes with the paste event, which works in any context; the async
// Clipboard API's readText is missing on a page served over plain HTTP from a
// network address, and may be refused anywhere. A paste that cannot be read
// has to say so rather than fail with a TypeError that only reaches the
// console.

const mountContext = (props = {}) =>
  mount(BaseClipboardContext, {
    props: { persistMessage: true, ...props },
    global: {
      stubs: { Message: { template: '<div role="status"><slot /></div>' } }
    }
  })

// A paste event, carrying `text` the way a browser's does - or no clipboard
// data at all, as an event raised by script does.
const paste = async (wrapper, text) => {
  const event = new Event('paste', { bubbles: true })
  if (text !== undefined) {
    Object.defineProperty(event, 'clipboardData', {
      value: { getData: (type) => (type === 'text/plain' ? text : '') }
    })
  }
  wrapper.element.dispatchEvent(event)
  await flushPromises()
}

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

  it('prefers the paste event to reading the clipboard', async () => {
    const readText = vi.fn().mockResolvedValue('stale')
    vi.stubGlobal('navigator', { clipboard: { readText } })
    const parse = vi.fn((text) => text)
    const wrapper = mountContext({ parse })

    await paste(wrapper, 'C6H6')

    expect(parse).toHaveBeenCalledWith('C6H6')
    expect(readText).not.toHaveBeenCalled()
  })

  it('says how to paste when there is nothing it can read', async () => {
    vi.stubGlobal('navigator', {})
    const parse = vi.fn()
    const wrapper = mountContext({ parse })

    await paste(wrapper)

    expect(parse).not.toHaveBeenCalled()
    expect(wrapper.emitted('validated')).toBeUndefined()
    expect(wrapper.text()).toContain('Could not read the clipboard')
    expect(wrapper.text()).toContain('Ctrl+V')
  })

  it('says the same when the browser refuses to read the clipboard', async () => {
    const readText = vi.fn().mockRejectedValue(new Error('denied'))
    vi.stubGlobal('navigator', { clipboard: { readText } })
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    const parse = vi.fn()
    const wrapper = mountContext({ parse })

    await paste(wrapper)

    expect(parse).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('Could not read the clipboard')
  })
})
