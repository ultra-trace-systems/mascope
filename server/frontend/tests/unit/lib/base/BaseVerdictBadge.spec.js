import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'

vi.mock('@/stores', () => ({ useApp: () => ({ auth: { user: { id: 7 } } }) }))

const { default: BaseVerdictBadge } = await import('@/lib/base/BaseVerdictBadge.vue')

const RECORD = {
  verdict: 'confirmed',
  evidence_level: 'msms',
  verified_by: 7,
  verified_utc: '2026-09-04T10:00:00Z',
  assigned_formula: 'C6H12O6'
}

// The tooltip directive's value, kept on the element so the text can be read.
const tooltip = {
  mounted(el, binding) {
    el.dataset.tooltip = binding.value
  },
  updated(el, binding) {
    el.dataset.tooltip = binding.value
  }
}
const TagStub = {
  props: ['value', 'severity', 'icon'],
  template: '<span class="tag"><slot name="icon" />{{ value }}</span>'
}
const mountBadge = (props) =>
  mount(BaseVerdictBadge, {
    props,
    global: { directives: { tooltip }, stubs: { Tag: TagStub } }
  })

describe('BaseVerdictBadge', () => {
  it('renders an owned compact verdict as its icon', () => {
    const wrapper = mountBadge({ record: RECORD, compact: true })
    const icon = wrapper.find('.verdict-icon')
    expect(icon.classes()).toContain('confirmed')
    expect(icon.classes()).not.toContain('inherited')
    expect(icon.attributes('data-tooltip')).toContain('Verified by you')
  })

  // A row nobody has judged carries no mark at all: not a faint seal, not an
  // empty pill. The ledgers' cells stay the buttons that judge it.
  it('draws nothing for an unjudged row, compact or not', () => {
    for (const props of [{ record: null }, { record: null, compact: true }]) {
      const wrapper = mountBadge(props)
      expect(wrapper.html()).toBe('<!--v-if-->')
    }
  })

  // Borrowed from the batch level: the same icon with the batch's mark - the
  // breadcrumb's - at its upper right. Not parenthesised and not dashed.
  it("draws a borrowed verdict as its own icon with the batch's mark, and says where it came from", () => {
    const wrapper = mountBadge({ record: RECORD, compact: true, inherited: true })
    const icon = wrapper.find('.verdict-icon.inherited')
    expect(icon.exists()).toBe(true)
    expect(icon.text()).toBe('')
    expect(icon.find('.ph-check-circle').exists()).toBe(true)
    expect(icon.find('[data-testid="batch-mark"]').classes()).toContain('pi-hashtag')
    const tip = icon.attributes('data-tooltip')
    expect(tip).toContain('Confirmed · MS/MS · batch')
    expect(tip).toContain('Batch-level verdict on C6H12O6, by you')
    expect(tip).toContain('verifying this sample records an exception')
  })

  it('adds a line for a batch-level verdict that disagrees with the owned one', () => {
    const wrapper = mountBadge({ record: RECORD, compact: true, conflict: { verdict: 'rejected' } })
    const icon = wrapper.find('.verdict-icon')
    expect(icon.classes()).toContain('conflict')
    expect(icon.attributes('data-tooltip')).toContain('Batch-level verdict differs: Rejected')
  })

  it('marks a borrowed tag as inherited, with the batch mark on its icon', () => {
    const wrapper = mountBadge({ record: RECORD, inherited: true })
    const tag = wrapper.find('.tag')
    expect(tag.classes()).toContain('inherited')
    expect(tag.text()).toBe('Confirmed · MS/MS · batch')
    expect(tag.find('.ph-check-circle').exists()).toBe(true)
    expect(tag.find('[data-testid="batch-mark"]').exists()).toBe(true)
  })

  it('draws an owned verdict without the batch mark', () => {
    expect(mountBadge({ record: RECORD }).find('[data-testid="batch-mark"]').exists()).toBe(false)
    const compact = mountBadge({ record: RECORD, compact: true })
    expect(compact.find('[data-testid="batch-mark"]').exists()).toBe(false)
  })

  it('shows the level and the structure a claim names', () => {
    const claim = {
      ...RECORD,
      evidence_level: 'reference_standard',
      claimed_inchikey: 'WQZGKKKJIJFFOK-GASJEMHNSA-N',
      claimed_name: 'D-glucose',
      level: 1
    }
    const tag = mountBadge({ record: claim }).find('.tag')
    expect(tag.text()).toBe('Confirmed · Level 1 · D-glucose')
    const tip = tag.attributes('data-tooltip')
    expect(tip).toContain('Evidence: Reference standard')
    expect(tip).toContain('Structure: D-glucose (WQZGKKKJIJFFOK-GASJEMHNSA-N)')
    expect(tip).toContain('Level 1, stated when the verdict was recorded')
  })

  it('names an unnamed structure by the first block of its InChIKey, borrowed or not', () => {
    const claim = { ...RECORD, claimed_inchikey: 'WQZGKKKJIJFFOK-GASJEMHNSA-N', level: 2 }
    const tag = mountBadge({ record: claim, inherited: true }).find('.tag')
    expect(tag.text()).toBe('Confirmed · Level 2 · WQZGKKKJIJFFOK… · batch')
  })

  it('cuts a long structure name to fit the pill', () => {
    const claim = {
      ...RECORD,
      claimed_inchikey: 'WQZGKKKJIJFFOK-GASJEMHNSA-N',
      claimed_name: '2,3,4,5,6-pentahydroxyhexanal monohydrate',
      level: 2
    }
    const tag = mountBadge({ record: claim }).find('.tag')
    expect(tag.text()).toBe('Confirmed · Level 2 · 2,3,4,5,6-pentahydroxyh…')
    expect(tag.attributes('data-tooltip')).toContain('pentahydroxyhexanal monohydrate')
  })

  it('shows no level on a verdict that states none', () => {
    const tag = mountBadge({ record: { ...RECORD, evidence_level: 'visual', level: null } }).find(
      '.tag'
    )
    expect(tag.text()).toBe('Confirmed · Visual')
    expect(tag.attributes('data-tooltip')).not.toContain('Level')
  })

  it('shows the letter beside the level, with what earned it and what would lift it', () => {
    const claim = {
      ...RECORD,
      evidence_level: 'reference_standard',
      claimed_inchikey: 'WQZGKKKJIJFFOK-GASJEMHNSA-N',
      claimed_name: 'D-glucose',
      level: 2,
      level_letter: 'b',
      context: {
        level_mapping_version: 2,
        claim: {
          evidence_detail: null,
          independent_line: null,
          evidence: 'an authentic standard on this instrument; no independent line',
          lift: 'an independent line'
        }
      }
    }
    const tag = mountBadge({ record: claim }).find('.tag')
    expect(tag.text()).toBe('Confirmed · Level 2b · D-glucose')
    const tip = tag.attributes('data-tooltip')
    expect(tip).toContain('Level 2b, stated when the verdict was recorded')
    expect(tip).toContain('Why: an authentic standard on this instrument; no independent line')
    expect(tip).toContain('Would lift it: an independent line')
  })

  it('names a class claim by its class', () => {
    const claim = {
      ...RECORD,
      claimed_inchikey: null,
      claimed_name: 'a carboxylic acid',
      level: 3,
      level_letter: 'b'
    }
    const tag = mountBadge({ record: claim }).find('.tag')
    expect(tag.text()).toBe('Confirmed · Level 3b · a carboxylic acid')
    expect(tag.attributes('data-tooltip')).toContain('Class: a carboxylic acid')
    expect(tag.attributes('data-tooltip')).not.toContain('Structure:')
  })

  it("says a level-1 batch claim's window, and why a sample outside it shows no level", () => {
    const claim = {
      ...RECORD,
      evidence_level: 'reference_standard',
      claimed_inchikey: 'WQZGKKKJIJFFOK-GASJEMHNSA-N',
      level: 1,
      window_start_utc: '2026-11-01T08:00:00Z',
      window_end_utc: '2026-11-08T08:00:00Z'
    }
    const inside = mountBadge({ record: claim, inherited: true }).find('.tag')
    expect(inside.text()).toContain('Level 1')
    expect(inside.attributes('data-tooltip')).toMatch(
      /Level 1 for the samples acquired from .+ to /
    )

    // As the server serves it outside the window: no structure and no level.
    const outside = mountBadge({
      record: {
        ...claim,
        claimed_inchikey: null,
        claimed_name: null,
        level: null,
        outside_window: true
      },
      inherited: true
    }).find('.tag')
    expect(outside.text()).not.toContain('Level')
    expect(outside.text()).not.toContain('WQZGKKKJIJFFOK')
    expect(outside.attributes('data-tooltip')).not.toContain('Structure:')
    expect(outside.attributes('data-tooltip')).toContain(
      "acquired outside the claim's window: it keeps the verdict on the formula, names no structure and reads the engine's level"
    )
  })

  it('says which claim a rejection on a standard refuted', () => {
    const rejection = {
      ...RECORD,
      verdict: 'rejected',
      evidence_level: 'reference_standard',
      level: null,
      context: {
        level_mapping_version: 2,
        refutes: { level: 1, level_letter: null, claimed_name: 'D-glucose' }
      }
    }
    const tag = mountBadge({ record: rejection }).find('.tag')
    expect(tag.text()).toBe('Rejected · refutes Level 1 · D-glucose')
    expect(tag.attributes('data-tooltip')).toContain(
      "A reference standard disagreed: refutes the confirmation at Level 1, D-glucose. The row reads the engine's level again"
    )
  })

  it('renders nothing without a record', () => {
    const wrapper = mountBadge({ record: null, compact: true })
    expect(wrapper.find('.verdict-icon').exists()).toBe(false)
    expect(wrapper.find('.tag').exists()).toBe(false)
  })
})
