import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { ref } from 'vue'

import PaneChemistryProfiles from '@/lib/panes/PaneChemistryProfiles.vue'

// The Chemistry settings open on the profiles Mascope ships, for a regular
// user: each row says it is shipped, what it calibrates on, and what it has
// routed. The server's own modes are not here; they are under Advanced.

const { modes, collections } = vi.hoisted(() => ({ modes: [], collections: [] }))

vi.mock('@/stores', () => ({
  useApp: () => ({
    data: {
      ionization: { mode: { list: modes } },
      target: { collection: { list: collections } }
    }
  })
}))

const LAST = '2026-11-05T14:02:00+00:00'

const profile = (id, name, polarity, calibrants, routed = 0, last = null) => ({
  ionization_mode_id: id,
  ionization_mode_name: name,
  ionization_mode_polarity: polarity,
  ionization_mode_token: null,
  system_key: id,
  calibration_collection_id: calibrants,
  routed_files: routed,
  last_file_utc: last
})

function mountPane() {
  const tableRows = ref([])
  return mount(PaneChemistryProfiles, {
    global: {
      directives: { tooltip: {} },
      stubs: {
        Tag: { props: ['value'], template: '<span class="tag">{{ value }}</span>' },
        DataTable: {
          props: ['value'],
          watch: { value: { handler: (value) => (tableRows.value = value), immediate: true } },
          template: '<div class="table"><slot /></div>'
        },
        Column: {
          props: ['header', 'field'],
          setup: () => ({ rows: tableRows }),
          template:
            '<div class="col" :data-header="header"><div v-for="row in rows" :key="row.id" ' +
            ':data-row="row.id"><slot name="body" :data="row">{{ row[field] }}</slot></div></div>'
        }
      }
    }
  })
}

const cell = (wrapper, header, id) =>
  wrapper.find(`.col[data-header="${header}"] [data-row="${id}"]`).text()

describe('PaneChemistryProfiles', () => {
  modes.splice(
    0,
    modes.length,
    {
      ionization_mode_id: 'own',
      ionization_mode_name: 'Bromide RI',
      ionization_mode_polarity: '-',
      ionization_mode_token: 'Br',
      system_key: null,
      calibration_collection_id: 'site-cal',
      routed_files: 40,
      last_file_utc: LAST
    },
    profile('sysDeprotonate', 'Deprotonation, negative', '-', null),
    profile('sysBromide', 'Bromide, negative', '-', 'sysBromideCal', 12, LAST),
    profile('sysUronium', 'Uronium, positive', '+', 'sysUroniumCal')
  )
  collections.splice(0, collections.length, {
    target_collection_id: 'sysBromideCal',
    target_collection_name: 'Bromide, negative calibrants'
  })

  it('lists the profiles alone, the ones that calibrate first', () => {
    const order = mountPane()
      .find('.col')
      .findAll('[data-row]')
      .map((row) => row.attributes('data-row'))
    expect(order).toEqual(['sysBromide', 'sysUronium', 'sysDeprotonate'])
  })

  it('says each is shipped', () => {
    const wrapper = mountPane()
    expect(cell(wrapper, 'Profile', 'sysBromide')).toBe('Bromide, negative Shipped')
    expect(cell(wrapper, 'Profile', 'sysDeprotonate')).toContain('Shipped')
  })

  it('says what each has routed, and when the newest file was acquired', () => {
    const wrapper = mountPane()
    expect(cell(wrapper, 'Files routed', 'sysBromide')).toBe('12 files')
    expect(cell(wrapper, 'Last file', 'sysBromide')).toBe(new Date(LAST).toLocaleString())
    expect(cell(wrapper, 'Files routed', 'sysUronium')).toBe('No files yet')
    expect(cell(wrapper, 'Last file', 'sysUronium')).toBe('-')
  })

  it('names the calibrants a profile ships, and says when it ships none', () => {
    const wrapper = mountPane()
    expect(cell(wrapper, 'Calibrants', 'sysBromide')).toBe('Bromide, negative calibrants')
    expect(cell(wrapper, 'Calibrants', 'sysDeprotonate')).toBe('None shipped')
    expect(cell(wrapper, 'Polarity', 'sysUronium')).toBe('Positive')
  })
})
