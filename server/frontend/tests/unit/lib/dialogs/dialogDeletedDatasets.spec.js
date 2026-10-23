import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import PrimeVue from 'primevue/config'

// The Deleted datasets dialog lists what the server gives it, read only, a
// page at a time: it reads the newest page each time it opens, says so when
// there is nothing and when the read failed, and opens a row's details on
// request.

vi.mock('@/api', () => ({ api: { http: { get: vi.fn() } } }))

import { api } from '@/api'
import { TOMBSTONE_PAGE_SIZE } from '@/lib/datasetTombstones'
import DialogDeletedDatasets from '@/lib/dialogs/DialogDeletedDatasets.vue'

const ButtonStub = {
  props: ['label', 'disabled', 'ariaLabel'],
  emits: ['click'],
  template: `<button :disabled="disabled" :aria-label="ariaLabel" @click="$emit('click')">{{ label }}</button>`
}
// The real DataTable and Column, since the columns and the row expansion are
// what is under test; the dialog's frame is not.
const STUBS = {
  Dialog: {
    props: ['visible', 'header'],
    template: '<div v-if="visible" class="dialog"><h2>{{ header }}</h2><slot /></div>'
  },
  Button: ButtonStub,
  ProgressSpinner: { template: '<span>Loading</span>' },
  BaseEmptyState: { props: ['title'], template: '<p class="empty">{{ title }}</p>' },
  BaseLoadError: {
    props: ['fallback', 'onRetry'],
    template: `<p class="load-error">{{ fallback }}<button @click="onRetry()">Try again</button></p>`
  }
}

const tombstone = (n, extra = {}) => ({
  dataset_id: `ds${n}`,
  dataset_name: `Dataset ${n}`,
  workspace_name: `Workspace ${n}`,
  deleted_utc: '2026-10-20T08:30:00+00:00',
  deleted_by: 'someone@example.org',
  deleted_via: 'dataset',
  n_sample_batches: 2,
  n_sample_items: 10,
  n_sample_files: 10,
  descriptive_metadata: null,
  ...extra
})

const serve = (pages, total) =>
  api.http.get.mockImplementation(async (url, { params }) => {
    expect(url).toBe('/datasets/tombstones')
    const answer = pages[params.page]
    if (answer instanceof Error) throw answer
    return { data: { data: answer ?? [], total } }
  })

const mountDialog = async ({ visible = true } = {}) => {
  const wrapper = mount(DialogDeletedDatasets, {
    props: { visible },
    global: { plugins: [PrimeVue], stubs: STUBS, directives: { tooltip: {} } }
  })
  await flushPromises()
  return { wrapper }
}

// Close and open again, as the Settings button and the dialog's close do.
const reopen = async (wrapper) => {
  await wrapper.setProps({ visible: false })
  await flushPromises()
  await wrapper.setProps({ visible: true })
  await flushPromises()
}

const bodyRows = (wrapper) => wrapper.findAll('tbody tr:not(.p-datatable-row-expansion)')
const cells = (row) => row.findAll('td').map((cell) => cell.text())

const button = (wrapper, label) => {
  const found = wrapper.find(`button[aria-label="${label}"]`)
  expect(found.exists(), `no "${label}" button`).toBe(true)
  return found
}

beforeEach(() => {
  api.http.get.mockReset()
})

afterEach(() => {
  document.body.innerHTML = ''
})

describe('DialogDeletedDatasets', () => {
  it('lists each deleted dataset: name, workspace, when, by whom, how, what it held', async () => {
    serve(
      [
        [
          tombstone(1),
          tombstone(2, {
            deleted_via: 'workspace',
            deleted_by: null,
            workspace_name: null,
            n_sample_batches: 0,
            n_sample_items: 0,
            n_sample_files: 0
          })
        ]
      ],
      2
    )
    const { wrapper } = await mountDialog()

    const headers = wrapper.findAll('thead th').map((th) => th.text())
    expect(headers).toEqual(['', 'Dataset', 'Workspace', 'Deleted', 'Held'])
    const rows = bodyRows(wrapper)
    expect(rows).toHaveLength(2)
    const when = new Date('2026-10-20T08:30:00+00:00').toLocaleString()
    expect(cells(rows[0]).slice(1)).toEqual([
      'Dataset 1',
      'Workspace 1',
      `${when} by someone@example.org`,
      '2 batches, 10 samples, 10 files'
    ])
    expect(cells(rows[1]).slice(1)).toEqual(['Dataset 2', '-', `${when} with its workspace`, '-'])
    // One page, so no pager.
    expect(wrapper.find('button[aria-label="Next page"]').exists()).toBe(false)
  })

  it('reads nothing while closed and reads afresh each time it opens', async () => {
    serve([[tombstone(1)]], 1)
    const { wrapper } = await mountDialog({ visible: false })
    expect(api.http.get).not.toHaveBeenCalled()

    await wrapper.setProps({ visible: true })
    await flushPromises()
    expect(api.http.get).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('Dataset 1')

    await reopen(wrapper)
    expect(api.http.get).toHaveBeenCalledTimes(2)
  })

  it('pages forward and back', async () => {
    const first = Array.from({ length: TOMBSTONE_PAGE_SIZE }, (_, i) => tombstone(i))
    serve([first, [tombstone(99)]], TOMBSTONE_PAGE_SIZE + 1)
    const { wrapper } = await mountDialog()

    expect(wrapper.text()).toContain(`1-${TOMBSTONE_PAGE_SIZE} of ${TOMBSTONE_PAGE_SIZE + 1}`)
    expect(button(wrapper, 'Previous page').attributes('disabled')).toBeDefined()

    await button(wrapper, 'Next page').trigger('click')
    await flushPromises()
    expect(api.http.get).toHaveBeenLastCalledWith(
      '/datasets/tombstones',
      expect.objectContaining({ params: { page: 1, limit: TOMBSTONE_PAGE_SIZE } })
    )
    expect(bodyRows(wrapper)).toHaveLength(1)
    expect(wrapper.text()).toContain('Dataset 99')
    expect(wrapper.text()).toContain(
      `${TOMBSTONE_PAGE_SIZE + 1}-${TOMBSTONE_PAGE_SIZE + 1} of ${TOMBSTONE_PAGE_SIZE + 1}`
    )
    expect(button(wrapper, 'Next page').attributes('disabled')).toBeDefined()

    await button(wrapper, 'Previous page').trigger('click')
    await flushPromises()
    expect(bodyRows(wrapper)).toHaveLength(TOMBSTONE_PAGE_SIZE)
  })

  it('opens on the newest page again when opened again', async () => {
    const first = Array.from({ length: TOMBSTONE_PAGE_SIZE }, (_, i) => tombstone(i))
    serve([first, [tombstone(99)]], TOMBSTONE_PAGE_SIZE + 1)
    const { wrapper } = await mountDialog()
    await button(wrapper, 'Next page').trigger('click')
    await flushPromises()

    await reopen(wrapper)
    expect(api.http.get).toHaveBeenLastCalledWith(
      '/datasets/tombstones',
      expect.objectContaining({ params: { page: 0, limit: TOMBSTONE_PAGE_SIZE } })
    )
    expect(bodyRows(wrapper)).toHaveLength(TOMBSTONE_PAGE_SIZE)
  })

  it('expands and collapses the details of a row, its descriptive metadata included', async () => {
    serve(
      [
        [
          tombstone(1, {
            dataset_description: 'Winter campaign',
            instrument: 'TOF',
            dataset_utc_created: '2026-01-02T03:04:00+00:00',
            dataset_utc_modified: '2026-02-03T04:05:00+00:00',
            descriptive_metadata: { license: 'CC-BY-4.0', authors: [{ name: 'A. Author' }] }
          }),
          tombstone(2)
        ]
      ],
      2
    )
    const { wrapper } = await mountDialog()
    expect(wrapper.find('dl.details').exists()).toBe(false)

    const toggles = wrapper.findAll('button.p-datatable-row-toggle-button')
    expect(toggles).toHaveLength(2)
    await toggles[0].trigger('click')
    expect(wrapper.findAll('dl.details')).toHaveLength(1)
    const details = wrapper.get('dl.details').text()
    expect(details).toContain('Winter campaign')
    expect(details).toContain('TOF')
    expect(details).toContain(`Created${new Date('2026-01-02T03:04:00+00:00').toLocaleString()}`)
    expect(details).toContain(`Modified${new Date('2026-02-03T04:05:00+00:00').toLocaleString()}`)
    expect(details).toContain('Licence')
    expect(details).toContain('CC-BY-4.0')
    expect(details).toContain('A. Author')
    expect(details).toContain('ds1')

    await wrapper.findAll('button.p-datatable-row-toggle-button')[0].trigger('click')
    expect(wrapper.find('dl.details').exists()).toBe(false)
  })

  it('says when there is nothing to list', async () => {
    serve([[]], 0)
    const { wrapper } = await mountDialog()
    expect(wrapper.get('.empty').text()).toBe('No deleted datasets')
  })

  it('shows a failed read in place of the list, with a retry', async () => {
    serve([new Error('down')], 0)
    const { wrapper } = await mountDialog()
    expect(wrapper.get('.load-error').text()).toContain('Could not load the deleted datasets.')

    serve([[tombstone(1)]], 1)
    await wrapper.get('.load-error button').trigger('click')
    await flushPromises()
    expect(wrapper.find('.load-error').exists()).toBe(false)
    expect(wrapper.text()).toContain('Dataset 1')
  })

  it('keeps the newest read when an older one answers late', async () => {
    let answerFirst
    api.http.get.mockImplementationOnce(() => new Promise((resolve) => (answerFirst = resolve)))
    const { wrapper } = await mountDialog()
    serve([[tombstone(2)]], 1)
    await reopen(wrapper)
    answerFirst({ data: { data: [tombstone(1)], total: 1 } })
    await flushPromises()
    expect(wrapper.text()).toContain('Dataset 2')
    expect(wrapper.text()).not.toContain('Dataset 1')
  })

  it('keeps the newest read when an older one fails late', async () => {
    let failFirst
    api.http.get.mockImplementationOnce(
      () => new Promise((resolve, reject) => (failFirst = reject))
    )
    const { wrapper } = await mountDialog()
    serve([[tombstone(2)]], 1)
    await reopen(wrapper)
    failFirst(new Error('down'))
    await flushPromises()
    expect(wrapper.find('.load-error').exists()).toBe(false)
    expect(wrapper.text()).toContain('Dataset 2')
  })
})
