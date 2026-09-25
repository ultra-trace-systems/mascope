import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

import PaneIonizationMechanism from '@/lib/panes/PaneIonizationMechanism.vue'

// The mechanism editor's hint: what is wrong with what is typed, else the one
// spelling the server will store when that is not what was typed.

vi.mock('@/stores', () => ({
  useApp: () => ({
    data: { ionization: { mechanism: { list: [], create: vi.fn(), delete: vi.fn() } } }
  })
}))
vi.mock('primevue/useconfirm', () => ({ useConfirm: () => ({ require: vi.fn() }) }))

const stubs = {
  InputText: {
    props: ['modelValue'],
    emits: ['update:modelValue'],
    template: `<input :value="modelValue" @input="$emit('update:modelValue', $event.target.value)" />`
  },
  FloatLabel: { template: '<div><slot /></div>' },
  Message: { template: '<p class="hint"><slot /></p>' },
  Button: true,
  DataTable: true,
  Column: true
}

async function hintFor(typed) {
  const wrapper = mount(PaneIonizationMechanism, {
    global: { stubs, directives: { tooltip: {} } }
  })
  await wrapper.find('input').setValue(typed)
  return wrapper.find('.hint').text()
}

describe('PaneIonizationMechanism', () => {
  it.each([
    ['[M+H+CH4N2O]+', 'Stored as [M+CH4N2O+H]+'],
    ['-H+', 'Stored as [M-H]-'],
    ['+(CH4N2O)H+', 'Stored as [M+CH4N2O+H]+']
  ])('says %s is stored as the one spelling of its mechanism', async (typed, hint) => {
    expect(await hintFor(typed)).toBe(hint)
  })

  it.each(['', '[M+CH4N2O+H]+', '  [M-H]-  '])(
    'gives examples when %j is stored as it is typed',
    async (typed) => {
      expect(await hintFor(typed)).toMatch(/^For example/)
    }
  )

  it('says what is wrong rather than what would be stored', async () => {
    expect(await hintFor('[M+H]')).toContain('singly charged')
    expect(await hintFor('[M+[15N]O3]-')).toContain('written with a caret')
  })
})
