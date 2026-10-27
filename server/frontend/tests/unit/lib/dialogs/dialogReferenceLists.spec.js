import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { reactive } from 'vue'
import PrimeVue from 'primevue/config'

// The Reference lists dialog shows the deployment's loaded lists, read only:
// one row per source with what the server says of it, a note on a source the
// licence gate leaves out, an empty state with the guide when nothing is
// loaded, and a row's species a page at a time when it is opened. It reads
// the list afresh each time it opens.

const store = vi.hoisted(() => ({ value: null }))
vi.mock('@/stores/data/modules/reference', () => ({ useReference: () => store.value }))

import { SPECIES_PAGE_SIZE } from '@/lib/referenceSources'
import DialogReferenceLists from '@/lib/dialogs/DialogReferenceLists'

const ButtonStub = {
  props: ['label', 'disabled', 'ariaLabel', 'href'],
  emits: ['click'],
  template: `<button :disabled="disabled" :aria-label="ariaLabel" :data-href="href" @click="$emit('click')">{{ label }}</button>`
}
// The real DataTable and Column, since the columns and the row expansion are
// what is under test; the dialog's frame is not.
const STUBS = {
  Dialog: {
    props: ['visible', 'header'],
    template: '<div v-if="visible" class="dialog"><h2>{{ header }}</h2><slot /></div>'
  },
  Button: ButtonStub,
  ProgressSpinner: { template: '<span class="spinner">Loading</span>' },
  BaseEmptyState: {
    props: ['title', 'hint'],
    template: '<div class="empty"><p>{{ title }}</p><p>{{ hint }}</p><slot name="actions" /></div>'
  },
  BaseLoadError: {
    props: ['fallback', 'onRetry'],
    template: `<p class="load-error">{{ fallback }}<button @click="onRetry()">Try again</button></p>`
  }
}

const source = (extra = {}) => ({
  name: 'monoterpene-hom-kang2021',
  version: '2026.09',
  label: 'Monoterpene HOM',
  license: 'CC-BY-4.0',
  polarity: 'negative',
  allow_radicals: false,
  known_window: null,
  record_count: 573,
  ingested_at: '2026-10-20T08:30:00+00:00',
  admitted: true,
  ...extra
})

const species = (n, extra = {}) => ({
  name: `Species ${n}`,
  formula: `C10H16O${n}`,
  monoisotopic_mass: 100 + n + 0.123456,
  inchikey: null,
  source_native_id: `s${n}`,
  reference: '10.5194/acp-21-1-2021',
  tags: [],
  ...extra
})

// A store as the dialog reads it: the list, its state, `load`, and `species`
// answering from the pages given, by page number.
const makeStore = ({ list = [], pages = [[]], total = 0 } = {}) => {
  const fake = reactive({ list, pending: false, error: null })
  fake.load = vi.fn(async () => {})
  fake.species = vi.fn(async (name, { page }) => {
    const answer = pages[page]
    if (answer instanceof Error) throw answer
    return { data: answer ?? [], total }
  })
  return fake
}

const mountDialog = async ({ visible = true } = {}) => {
  const wrapper = mount(DialogReferenceLists, {
    props: { visible },
    global: { plugins: [PrimeVue], stubs: STUBS, directives: { tooltip: {} } }
  })
  await flushPromises()
  return { wrapper }
}

const bodyRows = (wrapper) =>
  wrapper.findAll(
    '.dialog > .p-datatable > .p-datatable-table-container > table > tbody > tr:not(.p-datatable-row-expansion)'
  )
const cells = (row) => row.findAll(':scope > td').map((cell) => cell.text())
const expand = async (wrapper, index = 0) => {
  await wrapper.findAll('button.p-datatable-row-toggle-button')[index].trigger('click')
  await flushPromises()
}
const speciesRows = (wrapper) => wrapper.findAll('.species tbody tr')
const button = (wrapper, label) => {
  const found = wrapper.find(`button[aria-label="${label}"]`)
  expect(found.exists(), `no "${label}" button`).toBe(true)
  return found
}

beforeEach(() => {
  store.value = makeStore()
})

afterEach(() => {
  document.body.innerHTML = ''
})

describe('DialogReferenceLists', () => {
  it('lists each source: label, name, version, licence, polarity, radicals, window, species, loaded', async () => {
    store.value = makeStore({
      list: [
        source(),
        source({
          name: 'my-list',
          label: 'my-list',
          version: 'v1',
          license: 'custom',
          polarity: null,
          allow_radicals: true,
          known_window: { elements: ['C', 'H', 'O'], max_carbon: 20, max_mass: null },
          record_count: 12,
          ingested_at: null
        })
      ]
    })
    const { wrapper } = await mountDialog()

    const headers = wrapper.findAll('thead th').map((th) => th.text())
    expect(headers).toEqual([
      '',
      'List',
      'Version',
      'Licence',
      'Polarity',
      'Radicals',
      'Window',
      'Species',
      'Loaded'
    ])
    const rows = bodyRows(wrapper)
    expect(rows).toHaveLength(2)
    const when = new Date('2026-10-20T08:30:00+00:00').toLocaleString()
    expect(cells(rows[0]).slice(1)).toEqual([
      'Monoterpene HOMmonoterpene-hom-kang2021',
      '2026.09',
      'CC-BY-4.0',
      'Negative',
      'No',
      'Unbounded',
      '573',
      when
    ])
    // A source labelled by its own name shows the name once.
    expect(cells(rows[1]).slice(1)).toEqual([
      'my-list',
      'v1',
      'custom',
      'Both',
      'Allowed',
      'C, H, O; C <= 20; any mass',
      '12',
      '-'
    ])
    expect(wrapper.find('.gated').exists()).toBe(false)
  })

  it('notes a source outside the licence gate, with its licence', async () => {
    store.value = makeStore({
      list: [
        source(),
        source({ name: 'gated', label: 'Gated', license: 'CC-BY-NC-4.0', admitted: false })
      ]
    })
    const { wrapper } = await mountDialog()

    const rows = bodyRows(wrapper)
    expect(rows[0].find('.gated').exists()).toBe(false)
    const licence = rows[1].findAll(':scope > td')[3]
    expect(licence.text()).toContain('CC-BY-NC-4.0')
    expect(licence.get('.gated').text()).toContain('Not matched here')
  })

  it('says how a list is loaded when none is, and links the guide', async () => {
    const { wrapper } = await mountDialog()

    expect(wrapper.find('table').exists()).toBe(false)
    const empty = wrapper.get('.empty')
    expect(empty.text()).toContain('No reference lists loaded')
    expect(empty.text()).toContain('An operator loads them on the server')
    const link = empty.findAll('button').find((b) => b.text() === 'How to load a list')
    expect(link, 'no guide link').toBeDefined()
    expect(link.attributes('data-href')).toBe('/docs/guides/reference-lists/')
  })

  it('reads the list each time it opens, and not while closed', async () => {
    const { wrapper } = await mountDialog({ visible: false })
    expect(store.value.load).not.toHaveBeenCalled()

    await wrapper.setProps({ visible: true })
    await flushPromises()
    expect(store.value.load).toHaveBeenCalledTimes(1)

    await wrapper.setProps({ visible: false })
    await wrapper.setProps({ visible: true })
    await flushPromises()
    expect(store.value.load).toHaveBeenCalledTimes(2)
  })

  it('shows a failed read in place of the list, with a retry', async () => {
    store.value = makeStore({ list: [source()] })
    store.value.error = new Error('down')
    const { wrapper } = await mountDialog()

    expect(wrapper.get('.load-error').text()).toContain('Could not load the reference lists.')
    expect(wrapper.find('table').exists()).toBe(false)
    await wrapper.get('.load-error button').trigger('click')
    expect(store.value.load).toHaveBeenCalledTimes(2)
  })

  it('shows a spinner while the first read runs', async () => {
    store.value.pending = true
    const { wrapper } = await mountDialog()
    expect(wrapper.find('.spinner').exists()).toBe(true)
    expect(wrapper.find('.empty').exists()).toBe(false)
  })

  it("opens a row's species: name, formula, mass and the DOI, read for the row's version", async () => {
    store.value = makeStore({
      list: [source()],
      pages: [[species(1), species(2, { name: null, reference: 'S. Kang, thesis' })]],
      total: 2
    })
    const { wrapper } = await mountDialog()
    expect(store.value.species).not.toHaveBeenCalled()

    await expand(wrapper)
    expect(store.value.species).toHaveBeenCalledWith('monoterpene-hom-kang2021', {
      version: '2026.09',
      page: 0
    })
    const rows = speciesRows(wrapper)
    expect(rows).toHaveLength(2)
    expect(cells(rows[0])).toEqual(['Species 1', 'C10H16O1', '101.1235', '10.5194/acp-21-1-2021'])
    expect(rows[0].get('a').attributes('href')).toBe('https://doi.org/10.5194/acp-21-1-2021')
    // No name, and a reference that is not a DOI, shown as written.
    expect(cells(rows[1])).toEqual(['-', 'C10H16O2', '102.1235', 'S. Kang, thesis'])
    expect(rows[1].find('a').exists()).toBe(false)
    // One page, so no pager.
    expect(wrapper.find('button[aria-label="Next species page"]').exists()).toBe(false)
  })

  it('pages through the species forward and back', async () => {
    const first = Array.from({ length: SPECIES_PAGE_SIZE }, (_, i) => species(i))
    store.value = makeStore({
      list: [source()],
      pages: [first, [species(99)]],
      total: SPECIES_PAGE_SIZE + 1
    })
    const { wrapper } = await mountDialog()
    await expand(wrapper)

    expect(wrapper.text()).toContain(`1-${SPECIES_PAGE_SIZE} of ${SPECIES_PAGE_SIZE + 1}`)
    expect(button(wrapper, 'Previous species page').attributes('disabled')).toBeDefined()

    await button(wrapper, 'Next species page').trigger('click')
    await flushPromises()
    expect(store.value.species).toHaveBeenLastCalledWith('monoterpene-hom-kang2021', {
      version: '2026.09',
      page: 1
    })
    expect(speciesRows(wrapper)).toHaveLength(1)
    expect(wrapper.text()).toContain('Species 99')
    expect(button(wrapper, 'Next species page').attributes('disabled')).toBeDefined()

    await button(wrapper, 'Previous species page').trigger('click')
    await flushPromises()
    expect(speciesRows(wrapper)).toHaveLength(SPECIES_PAGE_SIZE)
  })

  it('shows a failed species read in the row, with a retry', async () => {
    store.value = makeStore({ list: [source()], pages: [new Error('down')], total: 0 })
    const { wrapper } = await mountDialog()
    await expand(wrapper)

    expect(wrapper.get('.load-error').text()).toContain('Could not load the species of this list.')
    store.value.species.mockResolvedValueOnce({ data: [species(1)], total: 1 })
    await wrapper.get('.load-error button').trigger('click')
    await flushPromises()
    expect(wrapper.find('.load-error').exists()).toBe(false)
    expect(speciesRows(wrapper)).toHaveLength(1)
  })
})
