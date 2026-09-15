import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'

import BaseLoadError from '@/lib/base/BaseLoadError.vue'

// The surface every pane uses to explain a failed load. Its job is to say what
// went wrong in the backend's own words where there are any, and to offer the
// way back - a pane that only goes blank is the bug this replaces.

// A plain <button> stands in for PrimeVue's, so the click the pane wires up is
// the one exercised. It re-emits nothing: the @click lands as a fall-through
// native listener on the root, which is exactly how the real Button carries it.
const mountError = (props) =>
  mount(BaseLoadError, {
    props,
    global: {
      stubs: { Button: { template: '<button>Try again</button>' } }
    }
  })

describe('BaseLoadError', () => {
  it('shows the message the backend sent', () => {
    const wrapper = mountError({
      error: { response: { data: { error: 'Run is still importing' } } }
    })

    expect(wrapper.text()).toContain('Run is still importing')
  })

  it('falls back to the network error message, then to the caller fallback', () => {
    expect(mountError({ error: new Error('Network Error') }).text()).toContain('Network Error')

    // An error carrying neither: the pane decides what to call the thing that
    // did not load, so the fallback is a prop rather than a fixed string.
    const bare = mountError({ error: {}, fallback: 'Could not load this run.' })
    expect(bare.text()).toContain('Could not load this run.')
  })

  it('accepts a ready-made message string', () => {
    expect(mountError({ error: 'Nothing to show here' }).text()).toContain('Nothing to show here')
  })

  it('offers the retry only when there is one, and calls it', async () => {
    const withoutRetry = mountError({ error: new Error('boom') })
    expect(withoutRetry.find('button').exists()).toBe(false)

    const onRetry = vi.fn()
    const wrapper = mountError({ error: new Error('boom'), onRetry })
    await wrapper.find('button').trigger('click')

    expect(onRetry).toHaveBeenCalledTimes(1)
  })
})
