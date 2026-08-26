import { describe, it, expect, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { nextTick } from 'vue'

import PrimeVue from 'primevue/config'

// The "Add compounds" panel renders the collection picker, the compounds table
// and the manual-input form as three sibling v-if branches in one fragment, and
// Vue numbers the branch keys it injects itself (0, 1, 2...). The table carries
// its own remount key, so that key has to stay clear of those numbers -
// otherwise flipping the source toggle warns "Duplicate keys found during
// update: 0" (#1061).
//
// Panel is deliberately NOT stubbed: it wraps its content in a <Transition>,
// and it is that Transition which drops the v-if comment placeholders and so
// forces the keyed diff that runs Vue's duplicate-key check. A passthrough
// stub keeps the placeholders, patches the fragment as a block and never
// reaches the check - which is also why the warning's component trace on the
// issue starts at <BaseTransition>.

vi.mock('@/api', () => ({ api: { http: { get: vi.fn().mockResolvedValue([]) } } }))

vi.mock('primevue/useconfirm', () => ({ useConfirm: () => ({ require: vi.fn() }) }))

// The paste context drags in the whole base barrel; pasting is not what this
// test is about, only that it keeps wrapping the panel's changes table.
vi.mock('@/lib/base', () => ({
  BaseClipboardContext: { template: '<div><slot /></div>' }
}))

const noPt = () => ({})
vi.mock('@/stores', () => ({
  useApp: () => ({
    auth: { user: { role_id: 100 } },
    ui: {
      help: {
        left: noPt,
        right: noPt,
        bottom: noPt,
        docUrl: (path) => `/docs/${path}`,
        set: vi.fn()
      }
    },
    data: {
      batch: { focused: null, focusedId: null },
      dataset: { focused: null, focusedId: null, list: [] },
      workspace: {
        focusedId: 'w1',
        list: [{ workspace_id: 'w1', workspace_name: 'Scratch', is_system: false }]
      },
      target: {
        collection: {
          detailed: null,
          list: [],
          read: vi.fn(),
          create: vi.fn(),
          update: vi.fn(),
          delete: vi.fn()
        },
        compound: { list: [] }
      }
    }
  })
}))

import DialogTargetCollectionOp from '@/lib/dialogs/DialogTargetCollectionOp.vue'

// The defect is in the dialog's own slot content, so every other PrimeVue piece
// is reduced to a passthrough that still renders its default slot.
const PRIMEVUE = [
  'ConfirmDialog',
  'Dialog',
  'FloatLabel',
  'SelectButton',
  'Tabs',
  'TabList',
  'Tab',
  'TabPanels',
  'TabPanel',
  'RadioButton',
  'InputText',
  'Button',
  'Checkbox',
  'DataTable',
  'Column',
  'Message',
  'Listbox',
  'Select',
  'IconField',
  'InputIcon'
]
const stubs = Object.fromEntries(
  PRIMEVUE.map((name) => [
    name,
    {
      name,
      props: ['modelValue', 'selection', 'value', 'options', 'visible', 'dataKey'],
      emits: ['update:modelValue', 'update:selection', 'update:visible'],
      template: '<div><slot /></div>'
    }
  ])
)

// Passthrough stubs render every tab (the real Tabs is lazy), so the batches
// table is on screen too - pick the compounds one by its dataKey.
const compoundTables = (wrapper) =>
  wrapper
    .findAllComponents({ name: 'DataTable' })
    .filter((table) => table.props('dataKey') === 'target_compound_id')

describe('DialogTargetCollectionOp add-compounds panel', () => {
  it('does not log duplicate keys when the compound source toggles', async () => {
    const warnings = []
    const wrapper = mount(DialogTargetCollectionOp, {
      props: { action: null },
      global: {
        plugins: [PrimeVue],
        stubs,
        directives: { tooltip: {} },
        config: { warnHandler: (msg) => warnings.push(msg) }
      }
    })
    // visible (and init()) are driven by the action watcher, so set it after mount.
    await wrapper.setProps({ action: 'create' })
    await flushPromises()

    const toggle = wrapper
      .findAllComponents({ name: 'SelectButton' })
      .find((c) => (c.props('options') ?? []).some((o) => o.value === 'input'))
    expect(toggle, 'no compound-source toggle on screen').toBeTruthy()

    // The counts keep the test from passing vacuously: the compounds table has
    // to actually come and go, which is what re-diffs the panel's fragment.
    expect(compoundTables(wrapper)).toHaveLength(1)
    toggle.vm.$emit('update:modelValue', 'input')
    await nextTick()
    expect(compoundTables(wrapper)).toHaveLength(0)
    toggle.vm.$emit('update:modelValue', 'collection')
    await nextTick()
    expect(compoundTables(wrapper)).toHaveLength(1)

    // Filtered rather than asserting no warnings at all - the stubs declare a
    // trimmed prop surface and may warn about unrelated things.
    expect(warnings.filter((message) => /Duplicate keys/.test(message))).toEqual([])
  })
})
