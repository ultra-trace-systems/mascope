import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'

import { monoisotopicIsotope } from '@/lib/chem'

// The match tab draws the visualized ion's M0 first and the selected isotope
// beside it, and heads each figure with its isotope's label, counted from that
// same M0. A labelled reagent's atom is bracketed like any substituted isotope,
// so for the 15N-nitrate ion C9H16O7^N- the M0 is [15N]C9H16O7-, and the one
// isotope without a bracket, C9H16NO7-, is the reagent's unlabelled remainder:
// read off the brackets alone it was the "M0", and the ion's own line "[15N]";
// read off the row order, which is the backend's m/z order, it was the figure
// the tab drew first. The isotope rows carry no ion formula; the ion they belong
// to is the visualized one.
//
// Which isotope is the M0 is the store's answer (`visualized.isotopeMain`), so
// the mock derives it with the real rule and these cases read what the chart
// does with it; chem.spec.js pins the rule itself.

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
// The figure is Plotly's. The stub renders the names of the traces it was handed,
// so a case can read which slice of the stream each figure got.
vi.mock('@/lib/charts/BaseChartPlotly.vue', () => ({
  default: {
    props: ['id', 'title', 'data', 'layout', 'config', 'hideTitle'],
    computed: {
      traceNames() {
        return (this.data ?? []).map((trace) => trace.name).join(',')
      }
    },
    template: '<div class="plot" :data-traces="traceNames" />'
  }
}))

// The spectrum traces arrive per isotope in one flat stream, which the chart
// slices back apart by isotope id. Held here so a case can stream a real one.
let chartTraces
vi.mock('@/lib/charts/ChartMatchSpectra/data.js', () => ({
  useChartData: () => ({
    get traces() {
      return chartTraces
    }
  })
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

/** The heading of each figure: the ion's M0, then the selected isotope's. */
async function headings() {
  const wrapper = mount(ChartMatchSpectra, {
    props: { sidebarOpen: false, modelValue: { mode: 'average', max: null } },
    global: { stubs: { Tag: true } }
  })
  await wrapper.vm.$nextTick()
  return wrapper.findAll('.spectra-figure h3').map((heading) => heading.text())
}

/** The trace names each figure was handed, in figure order. */
async function figureTraceNames() {
  const wrapper = mount(ChartMatchSpectra, {
    props: { sidebarOpen: false, modelValue: { mode: 'average', max: null } },
    global: { stubs: { Tag: true } }
  })
  await wrapper.vm.$nextTick()
  return wrapper
    .findAll('.plot')
    .map((plot) => plot.attributes('data-traces'))
    .map((names) => (names ? names.split(',') : []))
}

beforeEach(() => {
  chartTraces = []
  visualized = {
    ion: null,
    isotopes: [],
    isotopeSelected: null,
    // The store's own getter, derived by the rule chem.spec.js pins.
    get isotopeMain() {
      return monoisotopicIsotope(this.isotopes, this.ion?.target_ion_formula)
    }
  }
})

describe('ChartMatchSpectra isotope headings', () => {
  it('counts a 15N-labelled ion from its labelled line, the remainder at 14N', async () => {
    const remainder = isotope(250.0932, 'C9H16NO7-')
    const m0 = isotope(251.0903, '[15N]C9H16O7-')
    visualized.ion = { target_ion_formula: 'C9H16O7^N-' }
    visualized.isotopes = [remainder, m0]
    // The M0 is drawn first whatever is selected, so the remainder is the figure
    // selection adds.
    visualized.isotopeSelected = remainder

    expect(await headings()).toEqual(['M0: 251.0903', '[14N]: 250.0932'])
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

describe('ChartMatchSpectra first figure', () => {
  it("draws a labelled ion's labelled line first, not the lightest isotope", async () => {
    visualized.ion = { target_ion_formula: 'C9H16O7^N-' }
    visualized.isotopes = [
      isotope(250.0932, 'C9H16NO7-'),
      isotope(251.0903, '[15N]C9H16O7-'),
      isotope(252.0936, '[13C][15N]C8H16O7-')
    ]

    expect(await headings()).toEqual(['M0: 251.0903'])
  })

  // The M0 of a bromine cluster is its lightest line, not its tallest, so the
  // figure drawn first is the one position alone already picked.
  it('draws the lightest line of an unlabelled bromine cluster first', async () => {
    visualized.ion = { target_ion_formula: 'CHBr4-' }
    visualized.isotopes = [
      isotope(328.6817, 'CHBr4-'),
      isotope(330.6797, '[81Br]CHBr3-'),
      isotope(332.6776, '[81Br]2CHBr2-')
    ]

    expect(await headings()).toEqual(['M0: 328.6817'])
  })

  // Selecting the M0 itself leaves one figure: the pair is the M0 and whatever
  // else is selected.
  it("draws one figure when the selected isotope is the ion's M0", async () => {
    const m0 = isotope(251.0903, '[15N]C9H16O7-')
    visualized.ion = { target_ion_formula: 'C9H16O7^N-' }
    visualized.isotopes = [isotope(250.0932, 'C9H16NO7-'), m0]
    visualized.isotopeSelected = m0

    expect(await headings()).toEqual(['M0: 251.0903'])
  })

  // An ion whose isotopes name no M0 - nothing to tell the lines apart - falls
  // back to the lightest one, which is where the tab opened before.
  it('falls back to the lightest isotope when none is the M0', async () => {
    visualized.ion = { target_ion_formula: 'C9H16O7^N-' }
    visualized.isotopes = [
      isotope(252.0936, '[13C][15N]C8H16O7-'),
      isotope(253.0937, '[13C]2[15N]C7H16O7-')
    ]

    expect(await headings()).toEqual(['[13C]: 252.0936'])
  })
})

// The traces of every isotope arrive in one flat stream, each isotope's group led
// by a trace carrying its id, and the chart slices the stream back apart by
// finding that lead. Now that the first figure is the ion's M0 rather than the
// stream's first group, the slicing is what says whether the figure drawn under
// the M0's heading holds the M0's spectrum.
describe('ChartMatchSpectra trace slicing', () => {
  /** A trace group as the stream carries one: the lead names the isotope. */
  const group = (isotopeId, count) =>
    Array.from({ length: count }, (_, i) => ({
      name: `${isotopeId}-${i}`,
      ...(i === 0 ? { target_isotope_id: isotopeId } : {})
    }))

  const REMAINDER = 'C9H16NO7-'
  const M0 = '[15N]C9H16O7-'

  beforeEach(() => {
    visualized.ion = { target_ion_formula: 'C9H16O7^N-' }
    visualized.isotopes = [isotope(250.0932, REMAINDER), isotope(251.0903, M0)]
  })

  it("gives the M0's figure the M0's group, not the stream's first", async () => {
    chartTraces = [...group(REMAINDER, 2), ...group(M0, 3)]

    expect(await figureTraceNames()).toEqual([[`${M0}-0`, `${M0}-1`, `${M0}-2`]])
  })

  it('gives each figure its own group when the remainder is selected too', async () => {
    visualized.isotopeSelected = visualized.isotopes[0]
    chartTraces = [...group(REMAINDER, 2), ...group(M0, 3)]

    expect(await figureTraceNames()).toEqual([
      [`${M0}-0`, `${M0}-1`, `${M0}-2`],
      [`${REMAINDER}-0`, `${REMAINDER}-1`]
    ])
  })

  it("draws the M0's figure empty while only another isotope has streamed", async () => {
    chartTraces = group(REMAINDER, 2)

    expect(await figureTraceNames()).toEqual([[]])
  })

  // With no lead trace anywhere, the search for this isotope and the search for
  // the next group both come back -1, and a slice from -1 would hand the figure
  // the stream's last trace under the M0's heading.
  it('draws the figure empty when no trace names an isotope at all', async () => {
    chartTraces = [{ name: 'orphan-a' }, { name: 'orphan-b' }]

    expect(await figureTraceNames()).toEqual([[]])
  })
})
