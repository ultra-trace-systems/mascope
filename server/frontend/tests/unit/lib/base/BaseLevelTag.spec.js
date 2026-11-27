import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'

import BaseLevelTag from '@/lib/base/BaseLevelTag.vue'
import { LEVEL_META } from '@/lib/levels'

// The identification-level chip every level surface renders: the inspector,
// the sample ledger and the batch ledger. It writes the level inside its
// colour, so the colour is never alone, and it draws nothing for a row with no
// level - such a row was never judged, and a grey chip would read as 5b.

const mountTag = (props) =>
  mount(BaseLevelTag, {
    props,
    global: {
      // Writes the hover text onto the element, so a test reads what it says.
      directives: {
        tooltip: { mounted: (el, binding) => el.setAttribute('data-tooltip', binding.value ?? '') }
      }
    }
  })

describe('BaseLevelTag', () => {
  it('writes the level inside its colour', () => {
    const tag = mountTag({ level: '4a' }).find('.level')

    expect(tag.text()).toBe('4a')
    expect(tag.attributes('style')).toContain('--level-color: var(--level-formula)')
  })

  // The number decides the colour, in the level's own tokens; the letter is a
  // kind of evidence.
  it('colours by the number: a mass, a formula, a claim about structure', () => {
    const token = (level) => mountTag({ level }).find('.level').attributes('style')

    expect(token('5a')).toContain('var(--level-mass)')
    expect(token('5b')).toContain('var(--level-mass)')
    expect(token('4a')).toContain('var(--level-formula)')
    expect(token('4b')).toContain('var(--level-formula)')
    expect(token('3c')).toContain('var(--level-structure)')
    expect(token('2a')).toContain('var(--level-structure)')
    expect(token('1')).toContain('var(--level-structure)')
  })

  it('marks the chip with its number, the letters of one number alike', () => {
    expect(mountTag({ level: '4a' }).find('.level').classes()).toEqual(
      expect.arrayContaining(['level', 'level-4'])
    )
    expect(mountTag({ level: '4b' }).find('.level').classes()).toContain('level-4')
  })

  it('draws nothing for a row with no level, or one the scale does not name', () => {
    for (const level of [null, undefined, '', '6', 'constructor']) {
      const wrapper = mountTag({ level })
      expect(wrapper.find('.level').exists(), String(level)).toBe(false)
      expect(wrapper.find('.level-tag').exists(), String(level)).toBe(false)
    }
  })

  // Where a reader could be shown a claim's level or the engine's, the hover
  // says whose it is (readerLevel).
  it("says on hover whose level it is, and the engine's under a claim", () => {
    const claim = mountTag({ level: '2b', source: 'claim', engineLevel: '4a' })
    expect(claim.find('.level-tag').attributes('data-tooltip')).toContain(
      'Source: claim - the live verdict states this level; the engine read 4a'
    )
    const engine = mountTag({ level: '4a', source: 'engine' })
    expect(engine.find('.level-tag').attributes('data-tooltip')).toContain(
      'Source: engine - no live verdict states a level'
    )
    expect(mountTag({ level: '4a' }).find('.level-tag').attributes('data-tooltip')).not.toContain(
      'Source'
    )
  })

  // A level from the batch - a batch-level verdict's, or the batch peak's own
  // on a row the batch ledger serves - wears the batch's mark, the `#` an
  // inherited verdict wears, and its hover says whose it is.
  it("marks a level from the batch with the batch's mark, and says so on hover", () => {
    const batch = mountTag({ level: '4a', source: 'batch', fromBatch: true })
    expect(batch.find('[data-testid="level-batch-mark"]').classes()).toContain('pi-hashtag')
    expect(batch.find('.level-tag').classes()).toContain('from-batch')
    expect(batch.find('.level-tag').attributes('data-tooltip')).toContain(
      "Source: the batch - its batch peak's own level"
    )
    const claim = mountTag({ level: '1', source: 'claim', fromBatch: true })
    expect(claim.find('[data-testid="level-batch-mark"]').exists()).toBe(true)
    expect(claim.find('.level-tag').attributes('data-tooltip')).toContain(
      'Source: the batch - the batch-level verdict states this level'
    )
    // A run's level, or the sample's own verdict's, is unmarked.
    for (const props of [{ source: 'engine' }, { source: 'claim' }, {}]) {
      const own = mountTag({ level: '4a', ...props })
      expect(own.find('[data-testid="level-batch-mark"]').exists()).toBe(false)
      expect(own.find('.level-tag').classes()).not.toContain('from-batch')
    }
  })

  // A claim recorded under version 1 of the mapping has a number and no letter.
  it("draws a version-1 claim's bare number in its number's colour", () => {
    const tag = mountTag({ level: '3' }).find('.level')
    expect(tag.text()).toBe('3')
    expect(tag.attributes('style')).toContain('var(--level-structure)')
  })

  it('says on hover what the level means, what earned it and what would lift it', () => {
    const wrapper = mountTag({
      level: '5a',
      evidence: 'rivals not measured',
      lift: 'its rivals measured: this run did not measure them'
    })

    expect(wrapper.find('.level-tag').attributes('data-tooltip')).toBe(
      [
        LEVEL_META['5a'].description,
        'Why: rivals not measured',
        'Would lift it: its rivals measured: this run did not measure them'
      ].join('\n')
    )
  })

  it("says an isotopologue's level is its M0's", () => {
    const wrapper = mountTag({ level: '4a', role: 'iso_child' })

    expect(wrapper.find('.level-tag').attributes('data-tooltip')).toContain(
      'An isotopologue: it holds the level of its M0'
    )
  })

  it('yields to an explicit tooltip', () => {
    const wrapper = mountTag({ level: '4a', evidence: 'x', tooltip: 'say this instead' })

    expect(wrapper.find('.level-tag').attributes('data-tooltip')).toBe('say this instead')
  })
})

describe('BaseLevelTag outline', () => {
  // An importing engine's own level beside the members', as its tier chip is.
  it('outlines a second reading of the level', () => {
    expect(mountTag({ level: '4a', outlined: true }).classes()).toContain('outlined')
    expect(mountTag({ level: '4a' }).classes()).not.toContain('outlined')
  })
})
