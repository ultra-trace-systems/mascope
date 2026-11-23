import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

// Edit assignment settings (docs/dev/batch_assignment_one_action.md 3.4): the
// batch's own settings, saved on the batch. Pinned here with the real form
// inside it: the dialog opens on what the batch saved, and on the defaults
// where it saved nothing; Save sends the batch's settings alone, with the
// untouched defaults stripped; a viewer sees the form read-only.

const SERVED = {
  run_untargeted: true,
  profile: 'auto',
  context: 'auto',
  mz_precision_ppm: null,
  formula_ranges: null,
  max_untargeted_peaks: null,
  peak_intensity_threshold: 0,
  max_alternatives: 5
}

const PARAMS_RESPONSE = {
  data: {
    data: {
      params: {
        peak_assignment: SERVED,
        peak_assignment_limits: {
          max_untargeted_peaks_ceiling: 2000,
          max_mz_precision_ppm: 50,
          max_alternatives_ceiling: 20
        },
        peak_assignment_presets: {
          profiles: [{ name: 'BR', label: 'Bromide CIMS', polarity: '-' }],
          contexts: [{ name: 'ambient-air', label: 'Ambient air', description: '' }]
        }
      }
    }
  }
}

const get = vi.fn((url) => {
  if (url === '/params') return Promise.resolve(PARAMS_RESPONSE)
  return Promise.resolve({ data: { data: [] } })
})
vi.mock('@/api', () => ({ api: { http: { get: (...args) => get(...args) } } }))

const help = {
  set: vi.fn(),
  directive: () => ({ mounted() {}, unmounted() {} }),
  docUrl: (path = '') => `/docs/${path}`,
  right: () => ({})
}
const saveAssignmentSettings = vi.fn()
let workspace
let batchList
vi.mock('@/stores', () => ({
  useApp: () => ({
    ui: { help },
    auth: { user: { id: 'u-1' } },
    data: {
      workspace: { focused: workspace },
      batch: { list: batchList, saveAssignmentSettings }
    }
  })
}))

const DialogBatchAssignSettings = (await import('@/lib/dialogs/DialogBatchAssignSettings.vue'))
  .default

const stubs = {
  Dialog: {
    props: ['visible', 'header'],
    template:
      '<div v-if="visible" class="dialog"><h2>{{ header }}</h2><slot /><footer><slot name="footer" /></footer></div>'
  },
  Button: {
    props: ['label', 'disabled', 'loading'],
    emits: ['click'],
    template: '<button :disabled="disabled" @click="$emit(\'click\')">{{ label }}</button>'
  },
  ToggleSwitch: {
    props: ['modelValue'],
    template: '<input type="checkbox" class="toggle" :checked="modelValue" />'
  },
  InputNumber: {
    props: ['modelValue', 'inputId'],
    emits: ['update:modelValue'],
    template:
      '<input type="number" :id="inputId" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value === \'\' ? null : Number($event.target.value))" />'
  },
  InputText: {
    props: ['modelValue', 'invalid'],
    emits: ['update:modelValue'],
    template:
      '<input class="text" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" @blur="$emit(\'blur\')" />'
  },
  Select: {
    props: ['modelValue', 'options', 'inputId'],
    emits: ['update:modelValue'],
    template:
      '<select :id="inputId" :value="modelValue" @change="$emit(\'update:modelValue\', $event.target.value)"><option v-for="o in options" :key="o.value" :value="o.value">{{ o.label }}</option></select>'
  },
  Message: { template: '<div class="message"><slot /></div>' },
  FloatLabel: { template: '<div><slot /></div>' }
}

const BATCH = { sample_batch_id: 'sb-1', sample_batch_name: 'Batch 1' }

async function openDialog(assignment_settings = null) {
  const batch = { ...BATCH, assignment_settings }
  batchList = [batch]
  const wrapper = mount(DialogBatchAssignSettings, {
    props: { batch, visible: true },
    global: { stubs, directives: { tooltip: {} } }
  })
  await flushPromises()
  return wrapper
}

const button = (wrapper, label) => wrapper.findAll('button').find((b) => b.text() === label)
const numberValue = (wrapper, id) => wrapper.find(`#${id}`).element.value

async function typeNumber(wrapper, id, value) {
  await wrapper.find(`#${id}`).setValue(String(value))
}

beforeEach(() => {
  localStorage.clear()
  setActivePinia(createPinia())
  saveAssignmentSettings.mockReset()
  saveAssignmentSettings.mockResolvedValue({})
  help.set.mockClear()
  workspace = { workspace_id: 'w-1', my_role: 'editor' }
})

describe('DialogBatchAssignSettings', () => {
  it("opens on the batch's saved settings", async () => {
    const wrapper = await openDialog({ max_untargeted_peaks: 120, profile: 'BR' })

    expect(wrapper.find('h2').text()).toBe('Edit assignment settings')
    expect(numberValue(wrapper, 'max_untargeted_peaks')).toBe('120')
    expect(wrapper.find('select#assign_profile').element.value).toBe('BR')
    // The rest on the defaults.
    expect(numberValue(wrapper, 'max_alternatives')).toBe('5')
    // The pass is the untargeted search: no switch for it.
    expect(wrapper.find('.toggle').exists()).toBe(false)
    expect(button(wrapper, 'Cancel')).toBeDefined()
    expect(button(wrapper, 'Save')).toBeDefined()
  })

  it('opens on the defaults where the batch saved none', async () => {
    const wrapper = await openDialog(null)
    expect(numberValue(wrapper, 'max_untargeted_peaks')).toBe('')
    expect(numberValue(wrapper, 'max_alternatives')).toBe('5')
    expect(wrapper.find('select#assign_profile').element.value).toBe('auto')
  })

  it('reopens on what the batch saved, not on what was typed and cancelled', async () => {
    const wrapper = await openDialog({ max_alternatives: 2 })
    await typeNumber(wrapper, 'max_alternatives', 9)
    await button(wrapper, 'Cancel').trigger('click')
    expect(wrapper.emitted('update:visible').at(-1)).toEqual([false])

    await wrapper.setProps({ visible: false })
    await wrapper.setProps({ visible: true })
    await flushPromises()
    expect(numberValue(wrapper, 'max_alternatives')).toBe('2')
    expect(saveAssignmentSettings).not.toHaveBeenCalled()
  })

  it('saves the changed fields, the untouched defaults stripped', async () => {
    const wrapper = await openDialog(null)
    await typeNumber(wrapper, 'max_untargeted_peaks', 300)
    await button(wrapper, 'Save').trigger('click')
    await flushPromises()

    expect(saveAssignmentSettings).toHaveBeenCalledTimes(1)
    expect(saveAssignmentSettings.mock.calls[0][0]).toEqual({
      sample_batch_id: 'sb-1',
      assignment_settings: { max_untargeted_peaks: 300 }
    })
    expect(wrapper.emitted('saved')[0]).toEqual([{ max_untargeted_peaks: 300 }])
    expect(wrapper.emitted('update:visible').at(-1)).toEqual([false])
    // The row holds what was saved, so a reopen opens on it.
    expect(batchList[0].assignment_settings).toEqual({ max_untargeted_peaks: 300 })
  })

  it('keeps what the batch saved and nobody touched, as a PATCH replaces it whole', async () => {
    const wrapper = await openDialog({ max_alternatives: 2, profile: 'BR' })
    await typeNumber(wrapper, 'max_untargeted_peaks', 300)
    await button(wrapper, 'Save').trigger('click')
    await flushPromises()

    expect(saveAssignmentSettings.mock.calls[0][0].assignment_settings).toEqual({
      max_alternatives: 2,
      profile: 'BR',
      max_untargeted_peaks: 300
    })
  })

  it('drops a field put back on its default, and saves null for none left', async () => {
    const wrapper = await openDialog({ max_alternatives: 2 })
    await typeNumber(wrapper, 'max_alternatives', 5)
    await button(wrapper, 'Save').trigger('click')
    await flushPromises()

    expect(saveAssignmentSettings.mock.calls[0][0].assignment_settings).toBeNull()
  })

  it('sends nothing when nothing changed', async () => {
    const wrapper = await openDialog({ max_alternatives: 2 })
    await button(wrapper, 'Save').trigger('click')
    await flushPromises()

    expect(saveAssignmentSettings).not.toHaveBeenCalled()
    expect(wrapper.emitted('saved')).toBeUndefined()
    expect(wrapper.emitted('update:visible').at(-1)).toEqual([false])
  })

  it('commits a range still being typed, and refuses one that does not parse', async () => {
    const wrapper = await openDialog(null)
    await wrapper.find('input.text').setValue('C0-')
    await button(wrapper, 'Save').trigger('click')
    await flushPromises()
    expect(saveAssignmentSettings).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="settings-error"]').text()).toContain('formula range')

    await wrapper.find('input.text').setValue('C0-20 H0-40')
    await button(wrapper, 'Save').trigger('click')
    await flushPromises()
    expect(saveAssignmentSettings.mock.calls[0][0].assignment_settings).toEqual({
      formula_ranges: 'C0-20 H0-40'
    })
  })

  it('stays open with the reason when the save is refused', async () => {
    saveAssignmentSettings.mockRejectedValue({
      response: { status: 403, data: { error: 'The batch is locked.' } }
    })
    const wrapper = await openDialog(null)
    await typeNumber(wrapper, 'max_alternatives', 3)
    await button(wrapper, 'Save').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="settings-error"]').exists()).toBe(true)
    expect(wrapper.emitted('saved')).toBeUndefined()
    expect(wrapper.emitted('update:visible')).toBeUndefined()
  })

  it('shows a viewer the form read-only, with the reason', async () => {
    workspace = { workspace_id: 'w-1', my_role: 'viewer' }
    const wrapper = await openDialog({ max_alternatives: 2 })

    expect(wrapper.find('[data-testid="config-readonly"]').text()).toContain(
      'Ask an editor of this workspace'
    )
    expect(wrapper.find('#max_alternatives').attributes('disabled')).toBeDefined()
    expect(numberValue(wrapper, 'max_alternatives')).toBe('2')
    expect(button(wrapper, 'Save')).toBeUndefined()
    expect(button(wrapper, 'Close')).toBeDefined()
  })

  it("puts the form's help cards on the launcher layer while open", async () => {
    const wrapper = await openDialog(null)
    expect(help.set).toHaveBeenLastCalledWith('dialog_peak_assign')
    await wrapper.setProps({ visible: false })
    expect(help.set).toHaveBeenLastCalledWith(null)
  })
})
