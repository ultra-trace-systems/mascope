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
    const wrapper = mountTag({ tier: 'candidate', evidence: 0.62, source: 'manual' })

    expect(wrapper.find('[data-testid="manual-mark"]').exists()).toBe(true)
  })

  it("leaves the engine's own sources unmarked", () => {
    for (const source of ['database', 'untargeted', null]) {
      const wrapper = mountTag({ tier: 'assigned', source })
      expect(wrapper.find('[data-testid="manual-mark"]').exists()).toBe(false)
    }
  })

  // The mark is additional to the tier, not a tier of its own: a curated row
  // still carries whatever tier its evidence earns under the run's bands, and
  // the chip has to keep saying so.
  it('keeps showing the tier it was given', () => {
    const wrapper = mountTag({ tier: 'assigned', evidence: 0.91, source: 'manual' })

    expect(wrapper.find('.tag').text()).toBe('assigned')
  })

  // A percentage beside the tier reads as the chance the assignment is right,
  // which the evidence is not. The chip names the tier; the hover text gives
  // the evidence it was banded on, named as the product it is.
  it('names the tier alone, and the evidence on hover as what it is', () => {
    const wrapper = mountTag({ tier: 'below_assignability', evidence: 0.38 })

    expect(wrapper.find('.tag').text()).toBe('below')
    expect(wrapper.vm.autoTooltip).toContain('Evidence: 38% (fit x plausibility)')
  })

  // A tier that was not derived from a single number carries no number. The
  // batch ledger's consensus tier is a weighted vote over member tiers, so it
  // passes none rather than borrowing one.
  it('shows the tier alone when it was given no evidence', () => {
    const wrapper = mountTag({ tier: 'assigned' })

    expect(wrapper.find('.tag').text()).toBe('assigned')
    expect(wrapper.vm.autoTooltip).not.toContain('Evidence')
  })

  it('explains in hover text that the next run supersedes it', () => {
    const wrapper = mountTag({ tier: 'candidate', source: 'manual' })

    expect(wrapper.vm.autoTooltip).toContain('by hand')
    expect(wrapper.vm.autoTooltip).toContain('superseded')
  })

  // A curated row still gets the hand at any tier its fit earns, including the
  // bottom one: choosing a formula that fits badly is a choice.
  it('keeps the hand on a hand-assigned row that fits badly', () => {
    const wrapper = mountTag({ tier: 'below_assignability', evidence: 0.1, source: 'manual' })

    expect(wrapper.find('[data-testid="manual-mark"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="demoted-mark"]').exists()).toBe(false)
  })

  // A caller-supplied tooltip still wins outright, as it did before.
  it('yields to an explicit tooltip', () => {
    const wrapper = mountTag({ tier: 'candidate', source: 'manual', tooltip: 'say this instead' })

    expect(wrapper.vm.autoTooltip).toBe('say this instead')
  })
})

// Curating a peak also strips the isotopologues of the formula its M0
// no longer holds, and the backend leaves source = 'manual' on each stripped row
// so the ledger's source filter shows the whole footprint of one override. Those
// rows are a person's doing without anyone having chosen a formula for them -
// they carry no formula at all - so they must not wear the mark or the sentence
// that says someone did.
describe('BaseTierTag demoted mark', () => {
  const demoted = { tier: 'unassigned', source: 'manual' }

  it('does not claim a person chose the formula of a row with none', () => {
    const wrapper = mountTag(demoted)

    expect(wrapper.find('[data-testid="manual-mark"]').exists()).toBe(false)
    expect(wrapper.vm.autoTooltip).not.toContain('chose this formula')
  })

  // Marked rather than left bare: without a mark the row is indistinguishable
  // from a peak the engine simply never proposed anything for, which is the
  // wrong answer for the person looking for where their assignment went.
  it('still marks it as something a person caused', () => {
    const wrapper = mountTag(demoted)

    expect(wrapper.find('[data-testid="demoted-mark"]').exists()).toBe(true)
    expect(wrapper.vm.autoTooltip).toContain('its M0 was reassigned by hand')
    expect(wrapper.vm.autoTooltip).toContain('superseded')
  })

  // The chip's own label comes from the bucketed tier, so the mark is decided on
  // the same bucketed value: a row whose tier this build does not recognise
  // renders as "unassigned", and a hand beside a chip reading Unassigned is the
  // exact contradiction this mark exists to avoid.
  it('agrees with the label when the tier is one this build does not know', () => {
    const wrapper = mountTag({ tier: 'identified_v0', source: 'manual' })

    expect(wrapper.find('.tag').text()).toContain('unassigned')
    expect(wrapper.find('[data-testid="demoted-mark"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="manual-mark"]').exists()).toBe(false)
  })

  // An unassigned peak the engine never proposed anything for is nobody's
  // decision, so neither mark belongs on it.
  it('leaves an unassigned row the engine produced unmarked', () => {
    for (const source of ['database', 'untargeted', null]) {
      const wrapper = mountTag({ tier: 'unassigned', source })
      expect(wrapper.find('[data-testid="demoted-mark"]').exists(), String(source)).toBe(false)
      expect(wrapper.find('[data-testid="manual-mark"]').exists(), String(source)).toBe(false)
    }
  })
})

// A reagent or artifact peak is accounted for by what made it. Its row sits at
// tier `unassigned` because no compound was assigned, and a chip saying so would
// read as a peak nothing explained - so the role is the chip.
describe('BaseTierTag reagent and artifact peaks', () => {
  const roleIcon = (wrapper) => wrapper.find('.role-icon')

  it('shows the role in place of the tier, with no evidence', () => {
    for (const role of ['reagent', 'artifact']) {
      const wrapper = mountTag({ tier: 'unassigned', evidence: 0.5, role, source: role })

      expect(wrapper.find('.tag').text()).toBe(role)
      expect(wrapper.find('.tag').text()).not.toContain('unassigned')
      // The chip is the role, so no second mark repeats it.
      expect(roleIcon(wrapper).exists()).toBe(false)
    }
  })

  // The class is what colours the chip: the role's own colour, the spectrum's,
  // rather than the recessive dashed style an unassigned chip wears.
  it("wears the role's class, not the tier's", () => {
    for (const role of ['reagent', 'artifact']) {
      const classes = mountTag({ tier: 'unassigned', role }).find('.tag').classes()

      expect(classes).toEqual(expect.arrayContaining(['role', role]))
      expect(classes).not.toContain('tier')
      expect(classes).not.toContain('unassigned')
    }
    const tier = mountTag({ tier: 'unassigned', role: 'unassigned' }).find('.tag').classes()
    expect(tier).toEqual(expect.arrayContaining(['tier', 'unassigned']))
    expect(tier).not.toContain('role')
  })

  it('says what made the peak, and that it is not counted as a compound', () => {
    const reagent = mountTag({ tier: 'unassigned', role: 'reagent', source: 'reagent' })
    expect(reagent.vm.autoTooltip).toBe(
      [
        "Reagent: an ion the ionization source makes of itself, not one of the sample's compounds",
        "Counted apart from the sample's compounds",
        'Source: reagent'
      ].join('\n')
    )

    const artifact = mountTag({ tier: 'unassigned', role: 'artifact' })
    expect(artifact.vm.autoTooltip).toBe(
      [
        'Artifact: a ringing side lobe of a very intense neighbouring peak, not a species',
        "Counted apart from the sample's compounds"
      ].join('\n')
    )
  })

  it('keeps the tier on an isotopologue, marked as one', () => {
    const wrapper = mountTag({ tier: 'assigned', evidence: 0.9, role: 'iso_child' })

    expect(wrapper.find('.tag').text()).toContain('assigned')
    expect(roleIcon(wrapper).exists()).toBe(true)
  })

  it('leaves a monoisotopic row and an unknown role on their tier', () => {
    for (const role of ['M0', 'unassigned', 'constructor', null]) {
      const wrapper = mountTag({ tier: 'candidate', role })
      expect(wrapper.find('.tag').text(), String(role)).toBe('candidate')
    }
  })

  it('yields to an explicit tooltip here too', () => {
    const wrapper = mountTag({ tier: 'unassigned', role: 'reagent', tooltip: 'say this' })
    expect(wrapper.vm.autoTooltip).toBe('say this')
  })
})
