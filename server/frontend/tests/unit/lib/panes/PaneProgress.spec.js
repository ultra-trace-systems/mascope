import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { reactive } from 'vue'

// The progress bar names a process in its tooltip the way the toasts and the
// log name it, with the process's message after it; the bar is the status.

const mocks = vi.hoisted(() => ({ app: null }))
vi.mock('@/stores', () => ({ useApp: () => mocks.app }))

import PaneProgress from '@/lib/panes/PaneProgress.vue'

// Records what the tooltip directive is bound with, in place of PrimeVue's.
const record = (el, { value }) => el.setAttribute('data-tooltip', value)
const tooltip = { mounted: record, updated: record }

const mountPane = (progress) => {
  mocks.app = reactive({ ui: { notification: { progress } } })
  return mount(PaneProgress, { global: { directives: { tooltip } } })
}

const process = (type, message, progress, process_id = type) => ({
  process_id,
  type,
  message,
  progress
})

describe('PaneProgress', () => {
  it('names a process as the toasts and the log name it', () => {
    const wrapper = mountPane([process('calibration_mz_fit', 'Fitting 3 of 12', 25)])

    expect(wrapper.get('.progress-bar').attributes('data-tooltip')).toBe(
      'Calibration fit: Fitting 3 of 12'
    )
  })

  it('names a process the map does not know by its type', () => {
    const wrapper = mountPane([process('compute_batch_peaks', 'Half way', 50)])

    expect(wrapper.get('.progress-bar').attributes('data-tooltip')).toBe(
      'Compute batch peaks: Half way'
    )
  })

  it('draws no bar for a process that has not started', () => {
    const wrapper = mountPane([process('file_processing', 'Queued', 0)])

    expect(wrapper.find('.progress-bar').exists()).toBe(false)
  })
})
