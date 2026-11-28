import { describe, it, expect } from 'vitest'
import { h } from 'vue'
import { mount } from '@vue/test-utils'

import BaseProvisionalMark from '@/lib/base/BaseProvisionalMark.vue'
import { TIERING_PROVISIONAL } from '@/lib/tiers'
import { seenAt, shownAt } from '../ledger/narrowRules.js'

// The provisional mark beside a tier heading. In a narrow ledger its word goes
// from sight and an asterisk shows in its place (lib/ledger/narrow.css), so
// the Tier header no longer holds its column wider than its cells; the word
// stays the mark's accessible name and the hover explains it. jsdom evaluates
// no container query: the narrow rules are applied to the mounted mark, as a
// browser would in a ledger of that width.

const directives = {
  tooltip: { mounted: (el, binding) => el.setAttribute('data-tooltip', binding.value ?? '') }
}

// The mark in a ledger's Tier header, where the narrow rules reach it.
const mountInLedger = (props) =>
  mount(
    {
      render: () =>
        h('div', { class: 'ledger' }, [
          h('th', { class: 'col-tier' }, [h(BaseProvisionalMark, props)])
        ])
    },
    { global: { directives } }
  )

describe('BaseProvisionalMark', () => {
  it('is the word alone, hover and all, unless asked to collapse', () => {
    const wrapper = mount(BaseProvisionalMark, { global: { directives } })
    const mark = wrapper.find('[data-testid="tiering-provisional"]')
    expect(mark.text()).toBe(TIERING_PROVISIONAL.label)
    expect(mark.attributes('data-tooltip')).toBe(TIERING_PROVISIONAL.tooltip)
    expect(wrapper.find('.provisional-short').exists()).toBe(false)
  })

  it('carries an asterisk the screen reader skips, when collapsible', () => {
    const wrapper = mount(BaseProvisionalMark, {
      props: { collapsible: true },
      global: { directives }
    })
    const word = wrapper.find('.provisional-word')
    const short = wrapper.find('.provisional-short')
    expect(word.text()).toBe(TIERING_PROVISIONAL.label)
    expect(word.attributes('aria-hidden')).toBeUndefined()
    expect(short.text()).toBe('*')
    expect(short.attributes('aria-hidden')).toBe('true')
  })

  // The word or the asterisk, never both and never neither, and the word on
  // the page at every width: the mark's name.
  it.each([
    [300, '*'],
    [600, '*'],
    [680, '*'],
    [681, TIERING_PROVISIONAL.label],
    [1200, TIERING_PROVISIONAL.label]
  ])('in a ledger of %i px shows %j', (width, seen) => {
    const wrapper = mountInLedger({ collapsible: true })
    const word = wrapper.find('.provisional-word')
    const short = wrapper.find('.provisional-short')
    expect(shownAt(word.element, width)).toBe(true)
    const inSight = [word, short].filter((part) => seenAt(part.element, width))
    expect(inSight.map((part) => part.text())).toEqual([seen])
    // The hover, on the mark itself, holds at every width.
    expect(seenAt(wrapper.find('[data-tooltip]').element, width)).toBe(true)
  })
})
