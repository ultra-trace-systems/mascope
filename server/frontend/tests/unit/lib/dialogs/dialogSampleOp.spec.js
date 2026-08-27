import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { reactive } from 'vue'

// A raw filename normally carries the token of the ionization mode it was
// acquired in, and the dialog preselects that mode. These cover what happens
// when it does not - the case that used to leave an empty, unusable dropdown.

const process = vi.fn()

const app = reactive({
  ui: { help: { set: vi.fn() } },
  data: {
    ionization: { mode: { list: [] } },
    batch: { focused: { sample_batch_id: 'b1', sample_batch_name: 'Batch' } },
    sample: { list: [], process, update: vi.fn() }
  }
})

vi.mock('@/stores', () => ({ useApp: () => app }))

// The template toolbar drags in the whole toolbar barrel and has nothing to do
// with the ionization mode.
vi.mock('@/lib/toolbars', () => ({
  ToolbarTemplate: { props: ['template', 'default'], template: '<div />' }
}))

import DialogSampleOp from '@/lib/dialogs/DialogSampleOp.vue'

const mode = (id, name, token, polarity) => ({
  ionization_mode_id: id,
  ionization_mode_name: name,
  ionization_mode_token: token,
  ionization_mode_polarity: polarity
})

const MODES = [
  mode('m1', 'Ammonium', 'NH4', '+'),
  mode('m2', 'Proton transfer', 'PTR', '+'),
  mode('m3', 'Nitrate', 'NO3', '-')
]

const acquisition = (overrides = {}) => ({
  sample_file_id: 'f1',
  filename: 'inst_NH4_001.raw',
  sample_item_name: 'Sample 1',
  polarity: '+',
  instrument: 'inst',
  ...overrides
})

const SelectStub = {
  name: 'Select',
  props: ['modelValue', 'options', 'inputId', 'placeholder', 'disabled'],
  emits: ['update:modelValue'],
  template: '<div class="select" />'
}
const ButtonStub = {
  name: 'Button',
  props: ['label', 'disabled'],
  template: '<button :disabled="disabled">{{ label }}</button>'
}
const MessageStub = { name: 'Message', props: ['severity'], template: '<p><slot /></p>' }
const Passthrough = (name) => ({ name, template: '<div><slot /></div>' })

const openDialog = async (item) => {
  const wrapper = mount(DialogSampleOp, {
    props: { item, action: null },
    global: {
      directives: { tooltip: {} },
      stubs: {
        Select: SelectStub,
        Button: ButtonStub,
        Message: MessageStub,
        Dialog: Passthrough('Dialog'),
        Panel: Passthrough('Panel'),
        ScrollPanel: Passthrough('ScrollPanel'),
        FloatLabel: Passthrough('FloatLabel'),
        InputText: { name: 'InputText', props: ['modelValue'], template: '<input>' }
      }
    }
  })
  await wrapper.setProps({ action: 'create' })
  await flushPromises()
  return wrapper
}

const ionizationSelect = (wrapper) =>
  wrapper
    .findAllComponents(SelectStub)
    .find((select) => select.props('inputId') === 'ionization-mode')
const polaritySelect = (wrapper) =>
  wrapper
    .findAllComponents(SelectStub)
    .find((select) => select.props('inputId') === 'polarity-type')
const saveButton = (wrapper) =>
  wrapper.findAllComponents(ButtonStub).find((button) => button.props('label') === 'Save')

describe('DialogSampleOp ionization mode', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    app.data.ionization.mode.list = MODES
  })

  it('preselects the mode whose token the filename carries', async () => {
    const wrapper = await openDialog(acquisition())
    expect(ionizationSelect(wrapper).props('modelValue')).toBe('m1')
    expect(saveButton(wrapper).props('disabled')).toBe(false)
  })

  it('offers the polarity its modes when the filename has no token', async () => {
    const wrapper = await openDialog(acquisition({ filename: 'inst_20240101_001.raw' }))
    const select = ionizationSelect(wrapper)
    expect(select.props('modelValue')).toBeNull()
    expect(select.props('options')).toEqual([
      { label: 'Ammonium', value: 'm1' },
      { label: 'Proton transfer', value: 'm2' }
    ])
    expect(select.props('placeholder')).toContain('No mode token in the filename')
  })

  it('blocks save until an unresolved mode is picked, then processes with it', async () => {
    const wrapper = await openDialog(acquisition({ filename: 'inst_20240101_001.raw' }))
    expect(saveButton(wrapper).props('disabled')).toBe(true)
    expect(wrapper.text()).toContain('No mode token in the filename')

    ionizationSelect(wrapper).vm.$emit('update:modelValue', 'm2')
    await flushPromises()

    expect(saveButton(wrapper).props('disabled')).toBe(false)
    await saveButton(wrapper).trigger('click')
    await flushPromises()
    expect(process).toHaveBeenCalledWith(expect.objectContaining({ ionization_mode_id: 'm2' }))
  })

  it('says so when the polarity has no mode configured at all', async () => {
    app.data.ionization.mode.list = [mode('m3', 'Nitrate', 'NO3', '-')]
    const wrapper = await openDialog(acquisition({ filename: 'inst_20240101_001.raw' }))
    expect(ionizationSelect(wrapper).props('options')).toEqual([])
    expect(saveButton(wrapper).props('disabled')).toBe(true)
    expect(wrapper.text()).toContain('No ionization mode configured for this polarity')
  })

  it('blames the ambiguity, not the filename, when two tokens match', async () => {
    const wrapper = await openDialog(acquisition({ filename: 'inst_NH4_PTR_001.raw' }))
    const select = ionizationSelect(wrapper)
    expect(select.props('modelValue')).toBeNull()
    expect(saveButton(wrapper).props('disabled')).toBe(true)
    // The filename carries both tokens, so the no-token wording would be a lie.
    expect(select.props('placeholder')).toContain('matches two mode tokens')
    expect(wrapper.text()).not.toContain('No mode token in the filename')
  })

  it('re-resolves the mode when a mixed-polarity file switches sides', async () => {
    const wrapper = await openDialog(
      acquisition({ filename: 'inst_NH4_NO3_001.raw', polarity: '+-' })
    )
    // Nothing to offer until the user commits to one polarity.
    expect(ionizationSelect(wrapper).props('options')).toEqual([])

    polaritySelect(wrapper).vm.$emit('update:modelValue', '+')
    await flushPromises()
    expect(ionizationSelect(wrapper).props('modelValue')).toBe('m1')

    polaritySelect(wrapper).vm.$emit('update:modelValue', '-')
    await flushPromises()
    expect(ionizationSelect(wrapper).props('modelValue')).toBe('m3')
  })
})
