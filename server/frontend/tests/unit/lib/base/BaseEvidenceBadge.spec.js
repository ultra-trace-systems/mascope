import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import BaseEvidenceBadge from '@/lib/base/BaseEvidenceBadge.vue'
import { TONES } from '@/lib/evidence/marks'

// The tooltip directive's value, kept on the element so the text can be read.
const tooltip = {
  mounted(el, binding) {
    el.dataset.tooltip = binding.value
  },
  updated(el, binding) {
    el.dataset.tooltip = binding.value
  }
}
const mountBadge = (props) =>
  mount(BaseEvidenceBadge, { props, global: { directives: { tooltip } } })

// One reading of each tone, with the name the badge must give it.
const CASES = [
  {
    tone: 'supports',
    mark: 'series',
    record: { unit: 'CH2', step: 1, depth: 1, root_formula: 'C5H10O6', units: ['CH2'] },
    name: "In the batch's CH2 series: one CH2 heavier than C5H10O6, which the batch assigns.",
    glyph: 'ph-stairs'
  },
  {
    tone: 'holds-back',
    mark: 'blend',
    record: { formulas: ['C10H16O4', 'C9H12N2O2'], axis: 'time' },
    name: 'Probable blend of C10H16O4 and C9H12N2O2, parted by acquisition time',
    glyph: 'ph-intersect'
  },
  {
    tone: 'drifted',
    mark: 'ratio',
    record: { continuity: { pairs: [], grades: { drift: 1 } }, continuity_grade: 'drift' },
    name: 'Ratio stability: Drift',
    glyph: 'ph-pulse'
  },
  {
    tone: 'neutral',
    mark: 'flatSeries',
    record: { verdict: 'flat', cv: 0.0812, share: 0.9167 },
    name: 'Flat series: cv 0.08 in 92 % of the samples',
    glyph: 'ph-equals'
  },
  {
    tone: 'human',
    mark: 'manual',
    record: { batch: true },
    name: 'Curated by hand for the whole batch',
    glyph: 'ph-hand-pointing'
  }
]

describe('BaseEvidenceBadge', () => {
  it('renders each tone with its glyph, colour and accessible name', () => {
    for (const { tone, mark, record, name, glyph } of CASES) {
      const badge = mountBadge({ mark, record }).find('.evidence-badge')
      expect(badge.attributes('role'), mark).toBe('img')
      expect(badge.attributes('aria-label'), mark).toBe(name)
      expect(badge.attributes('data-mark'), mark).toBe(mark)
      expect(badge.attributes('data-tone'), mark).toBe(tone)
      expect(badge.classes(), mark).toContain(`tone-${tone}`)
      // The colour is the registry's token, so the badge and the legend agree.
      expect(badge.attributes('style'), mark).toContain(`--tone: ${TONES[tone].color}`)
      const icon = badge.find('.ph')
      expect(icon.classes(), mark).toContain(glyph)
      expect(icon.attributes('aria-hidden'), mark).toBe('true')
    }
  })

  it("carries the registry's tooltip", () => {
    const badge = mountBadge({ mark: 'flatSeries', record: CASES[3].record }).find(
      '.evidence-badge'
    )
    expect(badge.attributes('data-tooltip')).toMatch(/the tier stands\.$/)
  })

  // Beside a line that says the reading in words, the badge is the glyph and
  // the tone alone: a name and a tooltip there would say it twice.
  it('drops its name and tooltip when quiet', () => {
    const badge = mountBadge({ mark: 'series', record: CASES[0].record, quiet: true }).find(
      '.evidence-badge'
    )
    expect(badge.attributes('aria-hidden')).toBe('true')
    expect(badge.attributes('role')).toBeUndefined()
    expect(badge.attributes('aria-label')).toBeUndefined()
    expect(badge.attributes('data-tooltip')).toBeUndefined()
    expect(badge.attributes('data-tone')).toBe('supports')
  })

  it('draws nothing without a record, or for a reading the registry does not know', () => {
    expect(mountBadge({ mark: 'series', record: null }).html()).toBe('<!--v-if-->')
    expect(mountBadge({ mark: 'constructor', record: { a: 1 } }).html()).toBe('<!--v-if-->')
  })

  // A person's act is an outline in the tone's own colour, the text colour,
  // never the accent, which means Mascope and the one primary action.
  it('outlines the human tone in its own colour and hard-codes no colour', () => {
    const source = readFileSync(
      join(import.meta.dirname, '../../../../src/lib/base/BaseEvidenceBadge.vue'),
      'utf8'
    )
    const style = source.slice(source.indexOf('<style'))
    expect(TONES.human.color).toBe('var(--p-text-color)')
    expect(style).toMatch(/\.tone-human \{[^}]*border-color: var\(--tone\);/)
    expect(style).not.toMatch(/--p-primary-color/)
    expect(style).not.toMatch(/#[0-9a-f]{3,8}\b/i)
  })

  it('passes a test id through to its root', () => {
    const wrapper = mount(BaseEvidenceBadge, {
      props: { mark: 'manual', record: { batch: true } },
      attrs: { 'data-testid': 'mark-manual' },
      global: { directives: { tooltip } }
    })
    expect(wrapper.find('[data-testid="mark-manual"]').classes()).toContain('evidence-badge')
  })
})
