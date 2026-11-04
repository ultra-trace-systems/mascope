import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { reactive, nextTick } from 'vue'

// The dataset menu's dialogs are opened through its store: "Prepare for
// publication" sets the dataset its dialog is for, and closing the dialog
// clears it, so the entry opens it again next time.

const menu = vi.hoisted(() => ({ store: null }))

vi.mock('@/lib/panes/PaneBrowserSample/stores', async () => {
  const { reactive } = await import('vue')
  menu.store = reactive({
    entries: [],
    row: null,
    dialog: { op: null, publish: null },
    clear: () => {},
    ref: null
  })
  return { useDatasetContextMenu: () => menu.store }
})

vi.mock('@/lib/dialogs', () => ({
  DialogDatasetOp: { name: 'DialogDatasetOp', props: ['action', 'dataset'], template: '<div />' },
  DialogDatasetPackage: {
    name: 'DialogDatasetPackage',
    props: ['dataset'],
    emits: ['update:dataset'],
    template: '<div />'
  }
}))

import DatasetContextMenu from '@/lib/panes/PaneBrowserSample/DatasetContextMenu.vue'

const DATASET = reactive({ dataset_id: 'ds-1', workspace_id: 'ws-1', dataset_name: 'Winter run' })

describe('DatasetContextMenu', () => {
  it('opens "Prepare for publication" on the dataset the menu chose, and closes it', async () => {
    const wrapper = mount(DatasetContextMenu, {
      global: { stubs: { ContextMenu: { template: '<div />' } } }
    })
    const dialog = wrapper.findComponent({ name: 'DialogDatasetPackage' })
    expect(dialog.props('dataset')).toBe(null)

    menu.store.dialog.publish = DATASET
    await nextTick()
    expect(dialog.props('dataset')).toEqual(DATASET)

    dialog.vm.$emit('update:dataset', null)
    await nextTick()
    expect(menu.store.dialog.publish).toBe(null)
  })
})
