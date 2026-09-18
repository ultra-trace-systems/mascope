import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'

// Each spectrum figure of the match tab is headed by its isotope's label, counted
// from the visualized ion's M0. A labelled reagent's atom is bracketed like any
// substituted isotope, so for the 15N-nitrate ion C9H16O7^N- the M0 is
// [15N]C9H16O7-, and the one isotope without a bracket, C9H16NO7-, is the
// reagent's unlabelled remainder: read off the brackets alone it was the "M0",
// and the ion's own line "[15N]". The isotope rows carry no ion formula; the
// ion they belong to is the visualized one.

let visualized

vi.mock('@/stores', () => ({
  useApp: () => ({
    data: {
      sample: { selected: [{ length: 10 }] },
      match: {
        visualized,
        params: {
          uiCategory: () => 0,
          ui: { peak_min_intensity: 0, mz_tolerance: 5, isotope_ratio_tolerance: 0.5 }
        }
      }
    },
    ui: { split: { right: 50 } }
  })
}))

vi.mock('@/lib/base', () => ({ BaseMatchTag: true }))
// The figure is Plotly's; only the heading above it is read here.
vi.mock('@/lib/charts/BaseChartPlotly.vue', () => ({
  default: { props: ['id', 'title', 'data', 'layout', 'config', 'hideTitle'], template: '<div />' }
}))
vi.mock('@/lib/charts/ChartMatchSpectra/data.js', () => ({
  useChartData: () => ({ traces: [] })
}))

const { default: ChartMatchSpectra } =
  await import('@/lib/charts/ChartMatchSpectra/ChartMatchSpectra.vue')

/** An isotope row as the match store holds one, in m/z order. */
const isotope = (mz, formula) => ({
  target_isotope_id: formula,
  target_isotope_formula: formula,
  mz,
  match: { sample_peak_intensity: 1000, match_mz_error: 0.4, match_abundance_error: 0.02 }
})

/** The heading of each figure: the first isotope's, then the selected one's. */
async function headings() {
  const wrapper = mount(ChartMatchSpectra, {
    props: { sidebarOpen: false, modelValue: { mode: 'average', max: null } },
    global: { stubs: { Tag: true } }
  })
  await wrapper.vm.$nextTick()
  return wrapper.findAll('.spectra-figure h3').map((heading) => heading.text())
}

beforeEach(() => {
  visualized = { ion: null, isotopes: [], isotopeSelected: null }
})

describe('ChartMatchSpectra isotope headings', () => {
  it('counts a 15N-labelled ion from its labelled line, the remainder at 14N', async () => {
    const remainder = isotope(250.0932, 'C9H16NO7-')
    const m0 = isotope(251.0903, '[15N]C9H16O7-')
    visualized.ion = { target_ion_formula: 'C9H16O7^N-' }
    visualized.isotopes = [remainder, m0]
    visualized.isotopeSelected = m0

    expect(await headings()).toEqual(['[14N]: 250.0932', 'M0: 251.0903'])
  })

  it('keeps the brackets of an unlabelled ion', async () => {
    const m0 = isotope(328.6817, 'CHBr4-')
    const tallest = isotope(332.6776, '[81Br]2CHBr2-')
    visualized.ion = { target_ion_formula: 'CHBr4-' }
    visualized.isotopes = [m0, isotope(330.6797, '[81Br]CHBr3-'), tallest]
    visualized.isotopeSelected = tallest

    expect(await headings()).toEqual(['M0: 328.6817', '[81Br]2: 332.6776'])
  })
})
