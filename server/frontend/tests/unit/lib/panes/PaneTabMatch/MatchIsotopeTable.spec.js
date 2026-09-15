import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { ref } from 'vue'

// The match tab's isotope table labels each isotope of the visualized ion by how
// it differs from the ion's M0. A labelled reagent's atom is bracketed like any
// substituted isotope, so for the 15N-nitrate ion C9H16O7^N- the M0 is
// [15N]C9H16O7-, and the one isotope without a bracket, C9H16NO7-, is the
// reagent's unlabelled remainder. Read off the brackets alone, the remainder was
// "M0" and the ion's own line "[15N]". The isotope rows carry no ion formula:
// the ion they belong to is the visualized one.

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

// PrimeVue's DataTable is what hands each row to a Column's #body slot, so the
// stubs pass the rows down to read the cells the table renders.
async function substitutions() {
  const tableRows = ref([])
  const wrapper = mount(MatchIsotopeTable, {
    global: {
      directives: { tooltip: {} },
      stubs: {
        ProgressSpinner: true,
        DataTable: {
          props: { value: { type: Array, default: () => [] } },
          watch: {
            value: { handler: (value) => (tableRows.value = value), immediate: true }
          },
          template: '<div class="datatable"><slot /></div>'
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
  return wrapper.findAll('[data-header="Substitution"] span').map((cell) => cell.text())
}

beforeEach(() => {
  visualized = { ion: null, isotopes: [], isotopeSelected: null }
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
