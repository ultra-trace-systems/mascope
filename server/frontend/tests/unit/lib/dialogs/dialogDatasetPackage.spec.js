import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises, enableAutoUnmount } from '@vue/test-utils'
import PrimeVue from 'primevue/config'
import RealDialog from 'primevue/dialog'

// "Prepare for publication" reads what the dataset's package would hold each
// time it opens, flags what the dataset lacks for a deposit, opens the dataset
// dialog on the record it read to fill that in and reads again when it
// closes, and builds the archive in the background, a gap or not.

vi.mock('@/api', () => ({ api: { http: { get: vi.fn(), post: vi.fn() } } }))
// The dataset dialog is stubbed below; its module still imports the stores.
vi.mock('@/stores', () => ({ useApp: () => ({ data: { dataset: {} } }) }))

import { api } from '@/api'
import DialogDatasetPackage from '@/lib/dialogs/DialogDatasetPackage.vue'

const ROW = { dataset_id: 'ds-1', workspace_id: 'ws-1', dataset_name: 'Winter run' }
const PREVIEW_PATH = '/workspaces/ws-1/datasets/ds-1/export/package/preview'
const EXPORT_PATH = '/workspaces/ws-1/datasets/ds-1/export/package'

const RECORD = {
  dataset_id: 'ds-1',
  dataset_name: 'Winter run',
  dataset_description: 'Ambient air.',
  dataset_type: 'ANALYSIS',
  license: 'CC-BY-4.0',
  authors: [{ name: 'Author One', orcid: '0000-0002-1825-0097', affiliation: 'Lab A' }],
  keywords: ['aerosol'],
  related_identifiers: null,
  funding: null
}

const preview = (extra = {}) => ({
  archive: 'mascope-package_ds-1_Winter_run.zip',
  dataset: RECORD,
  files: 12,
  raw_bytes: 27_900,
  raw_files: 3,
  raw_files_included: 2,
  raw_files_not_kept: ['file-c'],
  acquisition_records: 1,
  batches: [
    { sample_batch_id: 'b1', sample_batch_name: 'First batch', samples: 2 },
    { sample_batch_id: 'b2', sample_batch_name: 'Second batch', samples: 2 }
  ],
  target_collections: [
    { target_collection_id: 'c1', target_collection_name: 'Targets', used_as: ['matched'] }
  ],
  ionization_modes: [{ ionization_mode_id: 'm1', ionization_mode_name: 'Bromide' }],
  gaps: [],
  ...extra
})

const GAPS = [
  {
    field: 'license',
    message: 'No licence. Without one, nobody but the authors may reuse the data.'
  },
  { field: 'authors', message: 'No authors. A repository cites a deposit by its creators.' }
]

const ButtonStub = {
  props: ['label', 'disabled'],
  emits: ['click'],
  template: `<button :disabled="disabled" @click="$emit('click')">{{ label }}</button>`
}
// The dataset dialog has its own spec; here it only has to be opened on a
// dataset, and closed.
const DialogDatasetOpStub = {
  name: 'DialogDatasetOp',
  props: ['action', 'dataset'],
  emits: ['update:action'],
  template: '<div class="dataset-op" />'
}
const STUBS = {
  Dialog: {
    props: ['visible', 'header'],
    template: '<div v-if="visible" class="dialog"><h2>{{ header }}</h2><slot /></div>'
  },
  Button: ButtonStub,
  Message: { props: ['severity'], template: '<div :class="`message ${severity}`"><slot /></div>' },
  ProgressSpinner: { template: '<span class="spinner">Loading</span>' },
  BaseLoadError: {
    props: ['fallback', 'onRetry'],
    template: `<p class="load-error">{{ fallback }}<button @click="onRetry()">Try again</button></p>`
  },
  DialogDatasetOp: DialogDatasetOpStub
}

const SPRING_ROW = { ...ROW, dataset_id: 'ds-2', dataset_name: 'Spring run' }
const springPreview = () =>
  preview({
    dataset: { ...RECORD, dataset_id: 'ds-2', dataset_name: 'Spring run' },
    batches: [{ sample_batch_id: 'b3', sample_batch_name: 'Spring batch', samples: 5 }]
  })

// The second dataset's preview, from its own route.
const serve2 = (answer) =>
  api.http.get.mockImplementationOnce(async (url) => {
    expect(url).toBe('/workspaces/ws-1/datasets/ds-2/export/package/preview')
    return { data: { data: answer } }
  })

const serve = (...answers) => {
  for (const answer of answers) {
    api.http.get.mockImplementationOnce(async (url) => {
      expect(url).toBe(PREVIEW_PATH)
      if (answer instanceof Error) throw answer
      return { data: { data: answer } }
    })
  }
}

const mountDialog = async (dataset = ROW) => {
  const wrapper = mount(DialogDatasetPackage, {
    props: { dataset },
    global: { plugins: [PrimeVue], stubs: STUBS }
  })
  await flushPromises()
  return wrapper
}

const button = (wrapper, label) => {
  const found = wrapper.findAll('button').find((b) => b.text() === label)
  expect(found, `no "${label}" button`).toBeTruthy()
  return found
}
const hasButton = (wrapper, label) => wrapper.findAll('button').some((b) => b.text() === label)
const closed = (wrapper) =>
  (wrapper.emitted('update:dataset') ?? []).some(([value]) => value === null)
const datasetOp = (wrapper) => wrapper.findComponent({ name: 'DialogDatasetOp' })

beforeEach(() => {
  api.http.get.mockReset()
  api.http.post.mockReset()
})

afterEach(() => {
  document.body.innerHTML = ''
})
// Registered last, so it runs first: a real Dialog unbinds its document
// listener on unmount, and one left bound would hear the next test's Escape.
enableAutoUnmount(afterEach)

describe('DialogDatasetPackage', () => {
  it('shows the record as it stands and what the package will hold', async () => {
    serve(preview())
    const wrapper = await mountDialog()

    expect(api.http.get).toHaveBeenCalledTimes(1)
    const text = wrapper.text()
    expect(wrapper.find('h2').text()).toBe("Prepare 'Winter run' for publication")
    expect(text).toContain('Ambient air.')
    expect(text).toContain('CC-BY-4.0')
    expect(text).toContain('Author One, ORCID 0000-0002-1825-0097, Lab A')
    expect(text).toContain('2 batches, 4 samples')
    expect(text).toContain('First batch: 2 samples')
    expect(text).toContain('2 of 3 files, 27.9 kB')
    expect(text).toContain('file-c')
    expect(text).toContain('Targets (matched)')
    expect(text).toContain('Bromide')
    expect(text).toContain('mascope-package_ds-1_Winter_run.zip, 12 files')
    // Nothing missing: said so, and nothing to edit.
    expect(wrapper.find('.message.success').exists()).toBe(true)
    expect(wrapper.find('.message.warn').exists()).toBe(false)
    expect(hasButton(wrapper, 'Edit dataset')).toBe(false)
  })

  it('flags each gap, and opens the dataset dialog on the record it read', async () => {
    serve(preview({ gaps: GAPS }))
    const wrapper = await mountDialog()

    const warning = wrapper.find('.message.warn')
    expect(warning.findAll('li').map((li) => li.text())).toEqual(GAPS.map((g) => g.message))

    await button(wrapper, 'Edit dataset').trigger('click')
    expect(datasetOp(wrapper).props('action')).toBe('edit')
    expect(datasetOp(wrapper).props('dataset')).toEqual(RECORD)
  })

  it('reads the package again when the dataset dialog closes', async () => {
    const filled = { ...RECORD, license: 'CC0-1.0' }
    serve(preview({ gaps: GAPS }), preview({ dataset: filled }))
    const wrapper = await mountDialog()

    await button(wrapper, 'Edit dataset').trigger('click')
    expect(api.http.get).toHaveBeenCalledTimes(1)
    datasetOp(wrapper).vm.$emit('update:action', null)
    await flushPromises()

    expect(api.http.get).toHaveBeenCalledTimes(2)
    expect(wrapper.find('.message.warn').exists()).toBe(false)
    expect(wrapper.text()).toContain('CC0-1.0')
    // A second edit starts from what was saved, not from the row clicked.
    await flushPromises()
    expect(datasetOp(wrapper).props('dataset')).toEqual(filled)
  })

  it('offers no edit for a dataset whose only gap is that it holds no samples', async () => {
    serve(
      preview({
        batches: [],
        gaps: [
          { field: 'samples', message: 'No samples. The package would describe no measurement.' }
        ]
      })
    )
    const wrapper = await mountDialog()

    expect(wrapper.find('.message.warn').text()).toContain('No samples.')
    expect(hasButton(wrapper, 'Edit dataset')).toBe(false)
  })

  it('builds the package, gaps or not, and closes', async () => {
    serve(preview({ gaps: GAPS }))
    api.http.post.mockResolvedValue({})
    const wrapper = await mountDialog()

    expect(button(wrapper, 'Build package').attributes('disabled')).toBeUndefined()
    await button(wrapper, 'Build package').trigger('click')
    await flushPromises()

    expect(api.http.post).toHaveBeenCalledWith(
      EXPORT_PATH,
      {},
      expect.objectContaining({ use: 'process' })
    )
    expect(closed(wrapper)).toBe(true)
  })

  it('stays open when the build is refused', async () => {
    serve(preview())
    api.http.post.mockRejectedValue(new Error('403'))
    const wrapper = await mountDialog()

    await button(wrapper, 'Build package').trigger('click')
    await flushPromises()

    expect(closed(wrapper)).toBe(false)
    expect(button(wrapper, 'Build package').attributes('disabled')).toBeUndefined()
  })

  it('builds nothing before the package is read, and says so when it cannot be', async () => {
    serve(new Error('boom'), preview())
    const wrapper = await mountDialog()

    expect(wrapper.find('.load-error').text()).toContain(
      'Could not read what the package would hold.'
    )
    expect(button(wrapper, 'Build package').attributes('disabled')).toBeDefined()

    await wrapper.find('.load-error button').trigger('click')
    await flushPromises()
    expect(wrapper.find('.load-error').exists()).toBe(false)
    expect(wrapper.text()).toContain('2 batches, 4 samples')
  })

  it('shows nothing of the last dataset while it reads the next', async () => {
    serve(preview())
    const wrapper = await mountDialog()
    let answer
    api.http.get.mockImplementationOnce(() => new Promise((resolve) => (answer = resolve)))

    await wrapper.setProps({ dataset: { ...ROW, dataset_id: 'ds-2', dataset_name: 'Spring run' } })
    await flushPromises()

    expect(wrapper.find('.spinner').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('First batch')
    expect(button(wrapper, 'Build package').attributes('disabled')).toBeDefined()
    answer({ data: { data: preview({ dataset: { ...RECORD, dataset_name: 'Spring run' } }) } })
    await flushPromises()
    expect(wrapper.find('h2').text()).toBe("Prepare 'Spring run' for publication")
  })

  it('keeps the dataset it moved to when the last one answers late', async () => {
    let first
    api.http.get.mockImplementationOnce(() => new Promise((resolve) => (first = resolve)))
    const wrapper = await mountDialog()
    serve2(springPreview())

    await wrapper.setProps({ dataset: SPRING_ROW })
    await flushPromises()
    first({ data: { data: preview() } })
    await flushPromises()

    expect(wrapper.find('h2').text()).toBe("Prepare 'Spring run' for publication")
    expect(wrapper.text()).toContain('Spring batch: 5 samples')
    expect(wrapper.text()).not.toContain('First batch')
  })

  it('keeps the dataset it moved to when the last one fails late', async () => {
    let fail
    api.http.get.mockImplementationOnce(() => new Promise((_, reject) => (fail = reject)))
    const wrapper = await mountDialog()
    serve2(springPreview())

    await wrapper.setProps({ dataset: SPRING_ROW })
    await flushPromises()
    fail(new Error('boom'))
    await flushPromises()

    expect(wrapper.find('.load-error').exists()).toBe(false)
    expect(wrapper.text()).toContain('Spring batch: 5 samples')
  })

  it('starts one build however often Build package is clicked', async () => {
    serve(preview())
    let built
    api.http.post.mockImplementationOnce(() => new Promise((resolve) => (built = resolve)))
    const wrapper = await mountDialog()

    await button(wrapper, 'Build package').trigger('click')
    expect(button(wrapper, 'Build package').attributes('disabled')).toBeDefined()
    await button(wrapper, 'Build package').trigger('click')
    await flushPromises()
    expect(api.http.post).toHaveBeenCalledTimes(1)

    built({})
    await flushPromises()
    expect(closed(wrapper)).toBe(true)
  })

  it('reads the package again each time it opens', async () => {
    serve(preview(), preview())
    const wrapper = await mountDialog()

    await button(wrapper, 'Cancel').trigger('click')
    expect(closed(wrapper)).toBe(true)
    await wrapper.setProps({ dataset: null })
    await wrapper.setProps({ dataset: ROW })
    await flushPromises()

    expect(api.http.get).toHaveBeenCalledTimes(2)
  })
})

// PrimeVue's own Dialog, transitions and all: every open Dialog closes on an
// Escape anywhere in the document, not only the one on top.
describe('DialogDatasetPackage under the dataset dialog', () => {
  const DialogDatasetOpOpen = {
    name: 'DialogDatasetOp',
    components: { RealDialog },
    props: ['action', 'dataset'],
    emits: ['update:action'],
    template: `<RealDialog :visible="!!action" header="Edit dataset" modal
      @update:visible="$emit('update:action', null)" />`
  }

  const escape = async () => {
    document.dispatchEvent(new KeyboardEvent('keydown', { code: 'Escape', key: 'Escape' }))
    await flushPromises()
  }

  it('leaves Escape to the dataset dialog while it is open, then closes', async () => {
    serve(preview({ gaps: GAPS }), preview())
    const wrapper = mount(DialogDatasetPackage, {
      props: { dataset: ROW },
      attachTo: document.body,
      global: {
        plugins: [PrimeVue],
        stubs: { ...STUBS, Dialog: false, transition: false, DialogDatasetOp: DialogDatasetOpOpen }
      }
    })
    await flushPromises()
    // The dialog's content is teleported to the body, out of the wrapper's DOM.
    const edit = [...document.body.querySelectorAll('button')].find(
      (b) => b.textContent === 'Edit dataset'
    )
    edit.click()
    await flushPromises()
    expect(datasetOp(wrapper).props('action')).toBe('edit')

    await escape()
    expect(datasetOp(wrapper).props('action')).toBeNull()
    expect(closed(wrapper)).toBe(false)
    // Closing the dataset dialog reads the package again, as a save does.
    expect(api.http.get).toHaveBeenCalledTimes(2)

    await escape()
    expect(closed(wrapper)).toBe(true)
  })
})
