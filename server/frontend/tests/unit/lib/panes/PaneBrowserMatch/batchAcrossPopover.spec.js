import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'

import { markOf } from '@/lib/evidence/marks'

// The barrel pulls the app's runtime in; the badge alone is what is drawn here.
vi.mock('@/lib/base', async () => ({
  BaseEvidenceBadge: (await vi.importActual('@/lib/base/BaseEvidenceBadge.vue')).default
}))

const { default: BatchAcrossPopover } =
  await import('@/lib/panes/PaneBrowserMatch/BatchAcrossPopover.vue')

const READINGS = [
  {
    id: 'blend',
    record: { formulas: ['C6H12O6', 'C7H16O5'], axis: 'time', time: null, intensity: null },
    action: null
  },
  {
    id: 'ratio',
    record: { continuity_grade: 'stable', continuity: { pairs: [] } },
    action: { label: 'Ratio stability: open the pairs behind the grade' }
  }
]

const mountPopover = () =>
  mount(BatchAcrossPopover, {
    props: { readings: READINGS },
    global: { directives: { tooltip: {} } }
  })

describe('BatchAcrossPopover', () => {
  it('draws each reading as its badge, quiet, followed by its reading in words', () => {
    const wrapper = mountPopover()
    const items = wrapper.findAll('li')
    expect(items).toHaveLength(2)
    for (const [i, reading] of READINGS.entries()) {
      const badge = items[i].find('.evidence-badge')
      expect(badge.attributes('data-mark')).toBe(reading.id)
      // The line says the reading, so the badge says it no second time.
      expect(badge.attributes('aria-hidden')).toBe('true')
      expect(items[i].find('[data-testid="across-reading-text"]').text()).toBe(
        markOf(reading.id).tooltip(reading.record).trim()
      )
    }
  })

  it('makes a badge that acts a button named for its action, and emits it', async () => {
    const wrapper = mountPopover()
    expect(wrapper.find('[data-testid="across-act-blend"]').exists()).toBe(false)
    const ratio = wrapper.find('[data-testid="across-act-ratio"]')
    expect(ratio.element.tagName).toBe('BUTTON')
    expect(ratio.attributes('aria-label')).toBe('Ratio stability: open the pairs behind the grade')
    await ratio.trigger('click')
    expect(wrapper.emitted('act')).toEqual([[READINGS[1]]])
  })
})
