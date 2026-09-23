import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

import PrimeVue from 'primevue/config'

// Files that bind to no ionization mode wait in Raw files for someone to
// choose their chemistry. The dialog asks for one mode per polarity the
// selected files hold and hands the choice to the server, which processes the
// files under it and refuses any that have samples already.

const mocks = vi.hoisted(() => ({ post: vi.fn(), push: vi.fn(), modes: [] }))

vi.mock('@/api', () => ({ api: { http: { post: mocks.post } } }))
vi.mock('@/stores', () => ({
  useApp: () => ({
    data: { ionization: { mode: { list: mocks.modes } } },
    ui: { notification: { push: mocks.push } }
  })
}))

import DialogChooseChemistry from '@/lib/dialogs/DialogChooseChemistry.vue'

// Only the dialog's own logic is under test; PrimeVue's pieces are reduced to
// passthroughs that still render their slots and forward what they are given.
const passthrough = (name) => ({
  name,
  props: ['modelValue', 'visible', 'label', 'disabled', 'loading', 'options', 'inputId'],
  emits: ['update:modelValue', 'update:visible', 'click'],
  template: '<div><slot /><slot name="footer" /></div>'
})
const stubs = Object.fromEntries(
  ['Dialog', 'FloatLabel', 'Select', 'Button', 'Message'].map((name) => [name, passthrough(name)])
)

const mode = (id, name, token, polarity) => ({
  ionization_mode_id: id,
  ionization_mode_name: name,
  ionization_mode_token: token,
  ionization_mode_polarity: polarity
})

const file = (id, polarity) => ({
  sample_file_id: id,
  filename: `Orbi_${id}.raw`,
  polarity,
  processing_status: 'needs_chemistry'
})

const mountDialog = (files) =>
  mount(DialogChooseChemistry, {
    props: { files, visible: true },
    global: { plugins: [PrimeVue], stubs }
  })

const selects = (wrapper) => wrapper.findAllComponents({ name: 'Select' })
const button = (wrapper, label) =>
  wrapper.findAllComponents({ name: 'Button' }).find((b) => b.props('label') === label)
const processButton = (wrapper) => button(wrapper, 'Process')
const setupButton = (wrapper) => button(wrapper, 'Set up ionization modes')

describe('DialogChooseChemistry', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.modes.splice(
      0,
      mocks.modes.length,
      mode('no3', 'Nitrate', 'NO3', '-'),
      mode('br', 'Bromide', 'Br', '-'),
      mode('ur', 'Uronium', null, '+')
    )
  })

  it('asks for a mode for each polarity the files hold', () => {
    const wrapper = mountDialog([file('a', '-'), file('b', '+-')])

    const [negative, positive] = selects(wrapper)
    expect(selects(wrapper)).toHaveLength(2)
    expect(negative.props('inputId')).toBe('chemistry-negative')
    expect(negative.props('options')).toEqual([
      { label: 'Bromide (Br)', value: 'br' },
      { label: 'Nitrate (NO3)', value: 'no3' }
    ])
    expect(positive.props('options')).toEqual([{ label: 'Uronium', value: 'ur' }])
  })

  it('asks for one mode when the files hold one polarity', () => {
    const wrapper = mountDialog([file('a', '-'), file('b', '-')])

    expect(selects(wrapper)).toHaveLength(1)
  })

  it('processes only once every polarity has a mode', async () => {
    const wrapper = mountDialog([file('a', '+-')])
    expect(processButton(wrapper).props('disabled')).toBe(true)

    await selects(wrapper)[0].vm.$emit('update:modelValue', 'no3')
    expect(processButton(wrapper).props('disabled')).toBe(true)

    await selects(wrapper)[1].vm.$emit('update:modelValue', 'ur')
    expect(processButton(wrapper).props('disabled')).toBe(false)
  })

  it('hands the files and the chosen modes to the server', async () => {
    mocks.post.mockResolvedValue({
      data: { message: 'Processing 2 files under the chosen chemistry.', data: { refused: [] } }
    })
    const wrapper = mountDialog([file('a', '-'), file('b', '+-')])
    await selects(wrapper)[0].vm.$emit('update:modelValue', 'no3')
    await selects(wrapper)[1].vm.$emit('update:modelValue', 'ur')

    await processButton(wrapper).vm.$emit('click')
    await flushPromises()

    expect(mocks.post).toHaveBeenCalledWith(
      '/sample/files/bind',
      { sample_file_ids: ['a', 'b'], ionization_mode_ids: ['no3', 'ur'] },
      expect.anything()
    )
    expect(mocks.push).toHaveBeenCalledWith(
      expect.objectContaining({
        status: 'success',
        message: 'Processing 2 files under the chosen chemistry.'
      })
    )
    expect(wrapper.emitted('update:visible')).toEqual([[false]])
    expect(wrapper.emitted('submit')).toHaveLength(1)
  })

  it('warns when the server refused some of the files', async () => {
    mocks.post.mockResolvedValue({
      status: 207,
      data: {
        error: 'Partially succeeded: 1 could not be bound.',
        detail: { refused: [{ sample_file_id: 'b' }] }
      }
    })
    const wrapper = mountDialog([file('a', '-'), file('b', '-')])
    await selects(wrapper)[0].vm.$emit('update:modelValue', 'br')

    await processButton(wrapper).vm.$emit('click')
    await flushPromises()

    expect(mocks.push).toHaveBeenCalledWith(
      expect.objectContaining({
        status: 'warning',
        message: 'Partially succeeded: 1 could not be bound.'
      })
    )
  })

  it('stays open when the request fails', async () => {
    mocks.post.mockRejectedValue(new Error('422'))
    const wrapper = mountDialog([file('a', '-')])
    await selects(wrapper)[0].vm.$emit('update:modelValue', 'br')

    await processButton(wrapper).vm.$emit('click')
    await flushPromises()

    expect(wrapper.emitted('update:visible')).toBeUndefined()
    expect(wrapper.emitted('submit')).toBeUndefined()
  })

  it('says when no mode of a polarity is configured', () => {
    mocks.modes.splice(0, mocks.modes.length, mode('br', 'Bromide', 'Br', '-'))
    const wrapper = mountDialog([file('a', '+-')])

    const message = wrapper.findComponent({ name: 'Message' })
    expect(message.exists()).toBe(true)
    expect(message.text()).toContain('No positive ionization mode is configured yet.')
  })

  // A file is here because its name named no mode, so the mode it was run
  // under is often one nobody has configured - and the options say nothing
  // about adding it. The way there is offered whatever the options hold: a
  // list that is merely missing the right mode looks no different from a
  // full one.
  it('offers the ionization settings, with modes configured or without', () => {
    expect(setupButton(mountDialog([file('a', '-')])).exists()).toBe(true)

    mocks.modes.splice(0, mocks.modes.length)
    expect(setupButton(mountDialog([file('a', '-')])).exists()).toBe(true)
  })

  it('asks its owner for the ionization settings, and stays open', async () => {
    const wrapper = mountDialog([file('a', '-')])

    await setupButton(wrapper).vm.$emit('click')

    expect(wrapper.emitted('configure')).toHaveLength(1)
    // The owner closes it, having somewhere to bring it back to.
    expect(wrapper.emitted('update:visible')).toBeUndefined()
  })

  // Going to the settings is not giving up on the dialog: the whole point is
  // to come back and use what was set up, so a mode picked for the other
  // polarity has to still be there. Whether an open is that return is the
  // owner's to say - the dialog remembering it for itself would leave the
  // memory standing when the owner decided not to come back.
  describe('coming back from the ionization settings', () => {
    const reopen = async (wrapper, { resume = false } = {}) => {
      await wrapper.setProps({ visible: false })
      await wrapper.setProps({ visible: true, resume })
    }

    it('keeps the modes already chosen when told this is the return', async () => {
      const wrapper = mountDialog([file('a', '+-')])
      await selects(wrapper)[0].vm.$emit('update:modelValue', 'no3')

      await setupButton(wrapper).vm.$emit('click')
      await reopen(wrapper, { resume: true })

      expect(selects(wrapper)[0].props('modelValue')).toBe('no3')
    })

    it('drops a mode that is gone by the time it comes back', async () => {
      const wrapper = mountDialog([file('a', '-')])
      await selects(wrapper)[0].vm.$emit('update:modelValue', 'no3')
      await setupButton(wrapper).vm.$emit('click')

      // Deleted or renamed while the settings were open.
      mocks.modes.splice(0, mocks.modes.length, mode('br', 'Bromide', 'Br', '-'))
      await reopen(wrapper, { resume: true })

      expect(selects(wrapper)[0].props('modelValue')).toBe(null)
    })

    it('still opens clean when it was simply closed and opened again', async () => {
      const wrapper = mountDialog([file('a', '-')])
      await selects(wrapper)[0].vm.$emit('update:modelValue', 'no3')

      await reopen(wrapper)

      expect(selects(wrapper)[0].props('modelValue')).toBe(null)
    })

    // The visit can end without a return - the files may be gone or taken
    // over by then. Having asked for the settings must not colour the next
    // open, which is for whatever is selected by then.
    it('does not remember having asked, once the return does not come', async () => {
      const wrapper = mountDialog([file('a', '-')])
      await selects(wrapper)[0].vm.$emit('update:modelValue', 'no3')
      await setupButton(wrapper).vm.$emit('click')

      // No return: the owner reopens it later for a different selection.
      await reopen(wrapper)

      expect(selects(wrapper)[0].props('modelValue')).toBe(null)
    })
  })
})
