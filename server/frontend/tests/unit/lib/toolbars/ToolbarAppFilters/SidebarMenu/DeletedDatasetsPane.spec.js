import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

// The Deleted datasets tab lists what the server gives it, read only, a page
// at a time: it reads afresh each time it is shown, says so when there is
// nothing and when the read failed, and opens a row's details on request.

vi.mock('@/api', () => ({ api: { http: { get: vi.fn() } } }))
vi.mock('@/stores', () => ({
  useApp: () => ({ ui: { help: { set: vi.fn(), directive: () => ({}) } } })
}))

import { api } from '@/api'
import { TOMBSTONE_PAGE_SIZE } from '@/lib/datasetTombstones'
import DeletedDatasetsPane from '@/lib/toolbars/ToolbarAppFilters/SidebarMenu/DeletedDatasetsPane.vue'
import { useSidebarMenu } from '@/lib/toolbars/ToolbarAppFilters/SidebarMenu/state.js'

const ButtonStub = {
  props: ['label', 'disabled', 'ariaLabel'],
  emits: ['click'],
  template: `<button :disabled="disabled" :aria-label="ariaLabel" @click="$emit('click')">{{ label }}</button>`
}
const STUBS = {
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

const mountPane = async ({ tab = 'deleted' } = {}) => {
  const menu = useSidebarMenu()
  menu.open = true
  menu.tab = tab
  const wrapper = mount(DeletedDatasetsPane, {
    global: { stubs: STUBS, directives: { tooltip: {} } }
  })
  await flushPromises()
  return { wrapper, menu }
}

const button = (wrapper, label) => {
  const found = wrapper.find(`button[aria-label="${label}"]`)
  expect(found.exists(), `no "${label}" button`).toBe(true)
  return found
}

beforeEach(() => {
  setActivePinia(createPinia())
  api.http.get.mockReset()
})

afterEach(() => {
  document.body.innerHTML = ''
})

describe('DeletedDatasetsPane', () => {
  it('lists each deleted dataset: name, workspace, when, by whom, how, what it held', async () => {
    serve([[tombstone(1), tombstone(2, { deleted_via: 'workspace', deleted_by: null })]], 2)
    const { wrapper } = await mountPane()

    const items = wrapper.findAll('li.tombstone')
    expect(items).toHaveLength(2)
    const first = items[0].text()
    expect(first).toContain('Dataset 1')
    expect(first).toContain('in Workspace 1')
    expect(first).toContain(
      `Deleted on its own on ${new Date('2026-10-20T08:30:00+00:00').toLocaleString()}`
    )
    expect(first).toContain('by someone@example.org')
    expect(first).toContain('Held 2 batches, 10 samples, 10 files')
    const second = items[1].text()
    expect(second).toContain('Deleted with its workspace')
    expect(second).not.toContain(' by ')
    // One page, so no pager.
    expect(wrapper.find('button[aria-label="Next page"]').exists()).toBe(false)
  })

  it('reads nothing while hidden and reads afresh each time it is shown', async () => {
    serve([[tombstone(1)]], 1)
    const { wrapper, menu } = await mountPane({ tab: 'workspaces' })
    expect(api.http.get).not.toHaveBeenCalled()

    menu.tab = 'deleted'
    await flushPromises()
    expect(api.http.get).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('Dataset 1')

    menu.tab = 'about'
    await flushPromises()
    menu.tab = 'deleted'
    await flushPromises()
    expect(api.http.get).toHaveBeenCalledTimes(2)
  })

  it('pages forward and back', async () => {
    const first = Array.from({ length: TOMBSTONE_PAGE_SIZE }, (_, i) => tombstone(i))
    serve([first, [tombstone(99)]], TOMBSTONE_PAGE_SIZE + 1)
    const { wrapper } = await mountPane()

    expect(wrapper.text()).toContain(`1-${TOMBSTONE_PAGE_SIZE} of ${TOMBSTONE_PAGE_SIZE + 1}`)
    expect(button(wrapper, 'Previous page').attributes('disabled')).toBeDefined()

    await button(wrapper, 'Next page').trigger('click')
    await flushPromises()
    expect(api.http.get).toHaveBeenLastCalledWith(
      '/datasets/tombstones',
      expect.objectContaining({ params: { page: 1, limit: TOMBSTONE_PAGE_SIZE } })
    )
    expect(wrapper.findAll('li.tombstone')).toHaveLength(1)
    expect(wrapper.text()).toContain('Dataset 99')
    expect(wrapper.text()).toContain(
      `${TOMBSTONE_PAGE_SIZE + 1}-${TOMBSTONE_PAGE_SIZE + 1} of ${TOMBSTONE_PAGE_SIZE + 1}`
    )
    expect(button(wrapper, 'Next page').attributes('disabled')).toBeDefined()

    await button(wrapper, 'Previous page').trigger('click')
    await flushPromises()
    expect(wrapper.findAll('li.tombstone')).toHaveLength(TOMBSTONE_PAGE_SIZE)
  })

  it('opens on the newest page again when shown again', async () => {
    const first = Array.from({ length: TOMBSTONE_PAGE_SIZE }, (_, i) => tombstone(i))
    serve([first, [tombstone(99)]], TOMBSTONE_PAGE_SIZE + 1)
    const { wrapper, menu } = await mountPane()
    await button(wrapper, 'Next page').trigger('click')
    await flushPromises()

    menu.tab = 'about'
    await flushPromises()
    menu.tab = 'deleted'
    await flushPromises()
    expect(api.http.get).toHaveBeenLastCalledWith(
      '/datasets/tombstones',
      expect.objectContaining({ params: { page: 0, limit: TOMBSTONE_PAGE_SIZE } })
    )
    expect(wrapper.findAll('li.tombstone')).toHaveLength(TOMBSTONE_PAGE_SIZE)
  })

  it('opens and closes the details of a row, its descriptive metadata included', async () => {
    serve(
      [
        [
          tombstone(1, {
            dataset_description: 'Winter campaign',
            instrument: 'TOF',
            dataset_utc_created: '2026-01-02T03:04:00+00:00',
            dataset_utc_modified: '2026-02-03T04:05:00+00:00',
            descriptive_metadata: { license: 'CC-BY-4.0', authors: [{ name: 'A. Author' }] }
          })
        ]
      ],
      1
    )
    const { wrapper } = await mountPane()
    expect(wrapper.find('dl.details').exists()).toBe(false)

    await button(wrapper, 'Show details of Dataset 1').trigger('click')
    const details = wrapper.get('dl.details').text()
    expect(details).toContain('Winter campaign')
    expect(details).toContain('TOF')
    expect(details).toContain(`Created${new Date('2026-01-02T03:04:00+00:00').toLocaleString()}`)
    expect(details).toContain(`Modified${new Date('2026-02-03T04:05:00+00:00').toLocaleString()}`)
    expect(details).toContain('Licence')
    expect(details).toContain('CC-BY-4.0')
    expect(details).toContain('A. Author')
    expect(details).toContain('ds1')

    await button(wrapper, 'Hide details of Dataset 1').trigger('click')
    expect(wrapper.find('dl.details').exists()).toBe(false)
  })

  it('says when there is nothing to list', async () => {
    serve([[]], 0)
    const { wrapper } = await mountPane()
    expect(wrapper.get('.empty').text()).toBe('No deleted datasets')
  })

  it('shows a failed read in place of the list, with a retry', async () => {
    serve([new Error('down')], 0)
    const { wrapper } = await mountPane()
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
    const { wrapper, menu } = await mountPane()
    serve([[tombstone(2)]], 1)
    menu.tab = 'about'
    await flushPromises()
    menu.tab = 'deleted'
    await flushPromises()
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
    const { wrapper, menu } = await mountPane()
    serve([[tombstone(2)]], 1)
    menu.tab = 'about'
    await flushPromises()
    menu.tab = 'deleted'
    await flushPromises()
    failFirst(new Error('down'))
    await flushPromises()
    expect(wrapper.find('.load-error').exists()).toBe(false)
    expect(wrapper.text()).toContain('Dataset 2')
  })

  it('reloads from its header button', async () => {
    serve([[tombstone(1)]], 1)
    const { wrapper } = await mountPane()
    serve([[tombstone(2)]], 1)
    await button(wrapper, 'Reload deleted datasets').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Dataset 2')
  })
})
