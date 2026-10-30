import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { ref } from 'vue'

import { monoisotopicIsotope } from '@/lib/chem'

// The match tab's isotope table labels each isotope of the visualized ion by how
// it differs from the ion's M0, and shows the M0's row as the one it opens on. A
// labelled reagent's atom is bracketed like any substituted isotope, so for the
// 15N-nitrate ion C9H16O7^N- the M0 is [15N]C9H16O7-, and the one isotope without
// a bracket, C9H16NO7-, is the reagent's unlabelled remainder. Read off the
// brackets alone, the remainder was "M0" and the ion's own line "[15N]"; read off
// the row order, which is the backend's m/z order, the remainder was the row the
// tab opened on. The isotope rows carry no ion formula: the ion they belong to is
// the visualized one.
//
// Which isotope is the M0 is the store's answer (`visualized.isotopeMain`), so
// the mock derives it with the real rule and these cases read what the table does
// with it; chem.spec.js pins the rule itself.
//
// The DataTable is stubbed, so the stub - not PrimeVue - decides that `rowClass`
// is a function taking the row. That it is the prop PrimeVue calls, with that
// signature, was checked by hand against the installed primevue 4.5.4
// (`datatable/index.mjs`, where `rowClasses` calls `this.rowClass(this.rowData)`)
// and is not held by anything here.

let visualized

vi.mock('@/stores', () => ({
  useApp: () => ({
    data: { match: { visualized, params: { uiCategory: () => 0 } } }
  })
}))

vi.mock('@/lib/base', () => ({
  BaseMatchTag: true,
  BaseCopyableField: { props: ['field'], template: '<span>{{ field }}</span>' }
}))

const { default: MatchIsotopeTable } =
  await import('@/lib/panes/PaneTabMatch/MatchIsotopeTable.vue')

/** An isotope row as the ion aggregate endpoint returns one, in m/z order. */
const isotope = (mz, formula, relativeAbundance) => ({
  target_isotope_id: formula,
  target_isotope_formula: formula,
  mz,
  relative_abundance: relativeAbundance,
  match: null
})

// PrimeVue's DataTable is what hands each row to a Column's #body slot and what
// puts `rowClass` on each rendered `tr`, so the stubs do both: the rows go down
// to the columns to read the cells, and a `tr` per row carries the class the
// table would give it.
async function mountTable() {
  const tableRows = ref([])
  const wrapper = mount(MatchIsotopeTable, {
    global: {
      directives: { tooltip: {} },
      stubs: {
        ProgressSpinner: true,
        DataTable: {
          props: {
            value: { type: Array, default: () => [] },
            rowClass: { type: Function, default: () => '' }
          },
          watch: {
            value: { handler: (value) => (tableRows.value = value), immediate: true }
          },
          template:
            '<div class="datatable"><table><tbody>' +
            '<tr v-for="row in value" :key="row.target_isotope_id" :class="rowClass(row)">' +
            '<td>{{ row.target_isotope_formula }}</td></tr></tbody></table><slot /></div>'
        },
        Column: {
          props: ['header'],
          setup: () => ({ rows: tableRows }),
          template:
            '<div class="col" :data-header="header"><template v-for="(row, i) in rows" :key="i">' +
            '<slot name="body" :data="row" /></template></div>'
        }
      }
    }
  })
  await wrapper.vm.$nextTick()
  return wrapper
}

/** The "Substitution" cell of every row, in the order the table renders them. */
async function substitutions() {
  const wrapper = await mountTable()
  return wrapper.findAll('[data-header="Substitution"] span').map((cell) => cell.text())
}

/** The isotope formulas of the rows the table shows as the ones it opens on. */
async function mainRowFormulas() {
  const wrapper = await mountTable()
  return wrapper.findAll('tr.main-isotope-row').map((row) => row.text())
}

beforeEach(() => {
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

describe('MatchIsotopeTable substitution labels', () => {
  it('counts a 15N-labelled ion from its labelled line, the remainder at 14N', async () => {
    visualized.ion = { target_ion_formula: 'C9H16O7^N-' }
    visualized.isotopes = [
      isotope(250.0932, 'C9H16NO7-', 0.0204),
      isotope(251.0903, '[15N]C9H16O7-', 1),
      isotope(252.0936, '[13C][15N]C8H16O7-', 0.0973)
    ]

    expect(await substitutions()).toEqual(['[14N]', 'M0', '[13C]'])
  })

  // A TOF sample is matched against the low-resolution isotopes, where the M0
  // and the remainder's 13C line, 6 mDa apart, are one isotope named by both.
  it('reads a merged low-resolution isotope that holds the M0 as the M0', async () => {
    visualized.ion = { target_ion_formula: 'C9H16O7^N-' }
    visualized.isotopes = [
      isotope(250.0932, 'C9H16NO7-', 0.0204),
      isotope(251.0903, '[15N]C9H16O7-/[13C]C8H16NO7-', 1.002)
    ]

    expect(await substitutions()).toEqual(['[14N]', 'M0'])
  })

  it('keeps the brackets of an unlabelled ion', async () => {
    visualized.ion = { target_ion_formula: 'CHBr4-' }
    visualized.isotopes = [
      isotope(328.6817, 'CHBr4-', 0.176),
      isotope(330.6797, '[81Br]CHBr3-', 0.685),
      isotope(332.6776, '[81Br]2CHBr2-', 1)
    ]

    expect(await substitutions()).toEqual(['M0', '[81Br]', '[81Br]2'])
  })
})

describe('MatchIsotopeTable opening row', () => {
  it("marks a labelled ion's labelled line, not the lightest row", async () => {
    visualized.ion = { target_ion_formula: 'C9H16O7^N-' }
    visualized.isotopes = [
      isotope(250.0932, 'C9H16NO7-', 0.0204),
      isotope(251.0903, '[15N]C9H16O7-', 1),
      isotope(252.0936, '[13C][15N]C8H16O7-', 0.0973)
    ]

    expect(await mainRowFormulas()).toEqual(['[15N]C9H16O7-'])
  })

  // The M0 of a bromine cluster is its lightest line, not its tallest, so the
  // row the table opens on is the one position alone already picked.
  it('marks the lightest line of an unlabelled bromine cluster', async () => {
    visualized.ion = { target_ion_formula: 'CHBr4-' }
    visualized.isotopes = [
      isotope(328.6817, 'CHBr4-', 0.176),
      isotope(330.6797, '[81Br]CHBr3-', 0.685),
      isotope(332.6776, '[81Br]2CHBr2-', 1)
    ]

    expect(await mainRowFormulas()).toEqual(['CHBr4-'])
  })

  // A low-resolution line that merges the M0 with another isotopologue is the
  // M0 line, and the remainder below it is still not.
  it('marks a merged low-resolution line that holds the M0', async () => {
    visualized.ion = { target_ion_formula: 'C9H16O7^N-' }
    visualized.isotopes = [
      isotope(250.0932, 'C9H16NO7-', 0.0204),
      isotope(251.0903, '[15N]C9H16O7-/[13C]C8H16NO7-', 1.002)
    ]

    expect(await mainRowFormulas()).toEqual(['[15N]C9H16O7-/[13C]C8H16NO7-'])
  })

  // An ion whose isotopes name no M0 - nothing to tell the lines apart - falls
  // back to the lightest row, which is where the tab opened before.
  it('falls back to the lightest row when no isotope is the M0', async () => {
    visualized.ion = { target_ion_formula: 'C9H16O7^N-' }
    visualized.isotopes = [
      isotope(252.0936, '[13C][15N]C8H16O7-', 0.0973),
      isotope(253.0937, '[13C]2[15N]C7H16O7-', 0.0046)
    ]

    expect(await mainRowFormulas()).toEqual(['[13C][15N]C8H16O7-'])
  })
})

// Beside each line's r.a., the table shows what was measured: the line's
// intensity relative to the ion's most abundant line, in the r.a.'s units, and
// on an Orbitrap file the scans that ratio was read over, those where both
// lines are present.
describe('MatchIsotopeTable measured ratio', () => {
  const matched = (relative, scans) => ({
    sample_peak_intensity_relative: relative,
    ratio_scan_count: scans
  })

  /** The text of one column's cells, row by row. */
  async function cells(header) {
    const wrapper = await mountTable()
    return wrapper
      .find(`[data-header="${header}"]`)
      .findAll('span')
      .map((cell) => cell.text())
  }

  beforeEach(() => {
    visualized.ion = { target_ion_formula: 'C10H15O2-' }
    visualized.isotopes = [
      { ...isotope(167.1078, 'C10H15O2-', 1), match: matched(1, 240) },
      { ...isotope(168.1112, '[13C]C9H15O2-', 0.1093), match: matched(0.1107, 118) },
      // A TOF line, or one matched before the count was kept: a ratio, no count
      { ...isotope(169.112, '[18O]C10H15O-', 0.0041), match: matched(0.0039, null) },
      // Unmatched: a zero placeholder, which is not a measurement
      { ...isotope(169.1145, '[13C]2C8H15O2-', 0.0054), match: matched(0, null) }
    ]
  })

  it('shows the measured ratio of every line with a peak', async () => {
    expect(await cells('measured')).toEqual(['100.000%', '11.070%', '0.390%'])
  })

  it('shows the scans a ratio was read over where it was read scan by scan', async () => {
    expect(await cells('scans')).toEqual(['240', '118'])
  })

  // A bromine pair: the r.a. is a share of the whole envelope, so the measured
  // ratio to the most abundant line is put on the same footing by that line's r.a.
  it("reads the measured share in the r.a.'s units", async () => {
    visualized.ion = { target_ion_formula: 'CH2Br-' }
    visualized.isotopes = [
      { ...isotope(92.9345, 'CH2Br-', 0.5), match: matched(1, 4) },
      { ...isotope(94.9325, '[81Br]CH2-', 0.48), match: matched(0.97, 3) }
    ]

    expect(await cells('measured')).toEqual(['50.000%', '48.500%'])
  })

  it('shows a ratio of zero read over scans as a measurement', async () => {
    visualized.isotopes = [
      { ...isotope(167.1078, 'C10H15O2-', 1), match: matched(1, 240) },
      { ...isotope(168.1112, '[13C]C9H15O2-', 0.1093), match: matched(0, 0) }
    ]

    expect(await cells('measured')).toEqual(['100.000%', '0.000%'])
    expect(await cells('scans')).toEqual(['240', '0'])
  })
})
