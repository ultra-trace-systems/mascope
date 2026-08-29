import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'

import BaseTierTag from '@/lib/base/BaseTierTag.vue'

// The confidence chip every assignment surface renders. Its `source` is normally
// which stage won the peak, and a line of hover text is the right weight for
// that. A curated row is the exception: there the source is a PERSON, which is
// the least guessable thing about the row and the one a reader scanning a ledger
// has to be able to see without hovering every line.

const mountTag = (props) =>
  mount(BaseTierTag, {
    props,
    global: {
      stubs: { Tag: { props: ['value'], template: '<span class="tag">{{ value }}</span>' } },
      directives: { tooltip: {} }
    }
  })

describe('BaseTierTag manual mark', () => {
  it('marks a row a person decided', () => {
    const wrapper = mountTag({ tier: 'candidate', fitScore: 0.62, source: 'manual' })

    expect(wrapper.find('[data-testid="manual-mark"]').exists()).toBe(true)
  })

  it("leaves the engine's own sources unmarked", () => {
    for (const source of ['database', 'untargeted', null]) {
      const wrapper = mountTag({ tier: 'assigned', source })
      expect(wrapper.find('[data-testid="manual-mark"]').exists()).toBe(false)
    }
  })

  // The mark is additional to the tier, not a tier of its own: a curated row
  // still carries whatever tier its fit earns under the run's bands, and the
  // chip has to keep saying so.
  it('keeps showing the tier and fit it was given', () => {
    const wrapper = mountTag({ tier: 'assigned', fitScore: 0.91, source: 'manual' })

    expect(wrapper.find('.tag').text()).toContain('91%')
  })

  it('explains in hover text that the next run supersedes it', () => {
    const wrapper = mountTag({ tier: 'candidate', source: 'manual' })

    expect(wrapper.vm.autoTooltip).toContain('by hand')
    expect(wrapper.vm.autoTooltip).toContain('superseded')
  })

  // A caller-supplied tooltip still wins outright, as it did before.
  it('yields to an explicit tooltip', () => {
    const wrapper = mountTag({ tier: 'candidate', source: 'manual', tooltip: 'say this instead' })

    expect(wrapper.vm.autoTooltip).toBe('say this instead')
  })
})
