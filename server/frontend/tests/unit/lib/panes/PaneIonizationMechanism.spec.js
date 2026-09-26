import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { ref } from 'vue'

import PaneIonizationMechanism from '@/lib/panes/PaneIonizationMechanism.vue'

// The mechanism editor's hint: what is wrong with what is typed, else the one
// spelling the server will store when that is not what was typed. And the list
// below it, which offers no delete for a mechanism Mascope ships.

const { mechanisms } = vi.hoisted(() => ({ mechanisms: [] }))

vi.mock('@/stores', () => ({
  useApp: () => ({
    data: {
      ionization: { mechanism: { list: mechanisms, create: vi.fn(), delete: vi.fn() } }
    }
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

// PrimeVue's DataTable is what feeds each row to a Column's #body slot, so the
// table is stubbed to hand its rows to a Column that renders that slot per row.
function mountList(rows) {
  mechanisms.splice(0, mechanisms.length, ...rows)
  const tableRows = ref([])
  return mount(PaneIonizationMechanism, {
    global: {
      directives: { tooltip: {} },
      stubs: {
        ...stubs,
        Button: {
          props: ['label'],
          template: '<button :aria-label="label">{{ label }}</button>'
        },
        DataTable: {
          props: ['value'],
          watch: { value: { handler: (value) => (tableRows.value = value), immediate: true } },
          template: '<div class="table"><slot /></div>'
        },
        Column: {
          setup: () => ({ rows: tableRows }),
          template:
            '<div class="col"><div v-for="row in rows" :key="row.ionization_mechanism_id" ' +
            ':data-row="row.ionization_mechanism_id"><slot name="body" :data="row" /></div></div>'
        }
      }
    }
  })
}

describe('PaneIonizationMechanism list', () => {
  const row = (id, mechanism, shipped) => ({
    ionization_mechanism_id: id,
    ionization_mechanism: mechanism,
    ionization_mechanism_polarity: '-',
    shipped
  })

  it('offers no delete for a mechanism Mascope ships, and one for the others', () => {
    const wrapper = mountList([
      row('sysAddBrNeg', '[M+Br]-', true),
      row('ours', '[M+Cl]-', false)
    ])

    // Every column renders the row; only the last has a body.
    const has = (id, selector) =>
      wrapper.findAll(`[data-row="${id}"]`).some((cell) => cell.find(selector).exists())
    const deletable = 'button[aria-label="Delete mechanism"]'
    const locked = '[aria-label="Shipped with Mascope"]'
    expect(has('sysAddBrNeg', deletable)).toBe(false)
    expect(has('sysAddBrNeg', locked)).toBe(true)
    expect(has('ours', deletable)).toBe(true)
    expect(has('ours', locked)).toBe(false)
  })
})
