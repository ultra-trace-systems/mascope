import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'

import BaseClaimWindow from '@/lib/base/BaseClaimWindow.vue'
import { toLocalInput } from '@/lib/verification'

const said = (wrapper) => wrapper.emitted('update:modelValue').at(-1)[0]
const inputs = (wrapper) => wrapper.findAll('input[type="datetime-local"]')

describe('BaseClaimWindow', () => {
  it('starts from the batch acquisitions, the last second whole', () => {
    const wrapper = mount(BaseClaimWindow, {
      props: { range: { first: '2026-11-01T08:00:00Z', last: '2026-11-02T08:00:30Z' } }
    })
    // Shown in the reader's zone: the same instant read back.
    expect(new Date(inputs(wrapper)[0].element.value).toISOString()).toBe(
      '2026-11-01T08:00:00.000Z'
    )
    expect(said(wrapper)).toEqual({
      window_start_utc: '2026-11-01T08:00:00.000Z',
      window_end_utc: '2026-11-02T08:00:30.999Z',
      ready: true,
      problem: null
    })
  })

  it('starts from the window of the verdict being changed', () => {
    const wrapper = mount(BaseClaimWindow, {
      props: {
        initial: {
          window_start_utc: '2026-11-03T00:00:00Z',
          window_end_utc: '2026-11-04T00:00:00.999Z'
        },
        range: { first: '2026-11-01T08:00:00Z', last: '2026-11-08T08:00:00Z' }
      }
    })
    expect(said(wrapper)).toMatchObject({
      window_start_utc: '2026-11-03T00:00:00.000Z',
      window_end_utc: '2026-11-04T00:00:00.999Z'
    })
  })

  it('waits for both ends, in order', async () => {
    const wrapper = mount(BaseClaimWindow)
    expect(said(wrapper)).toMatchObject({ ready: false })
    expect(wrapper.text()).toContain('Give the first and last acquisition time')
    await inputs(wrapper)[0].setValue(toLocalInput('2026-11-05T00:00:00Z'))
    await inputs(wrapper)[1].setValue(toLocalInput('2026-11-04T00:00:00Z'))
    expect(said(wrapper)).toMatchObject({
      ready: false,
      problem: 'The first acquisition time is after the last'
    })
    await inputs(wrapper)[1].setValue(toLocalInput('2026-11-06T00:00:00Z'))
    expect(said(wrapper)).toMatchObject({ ready: true, problem: null })
  })
})
