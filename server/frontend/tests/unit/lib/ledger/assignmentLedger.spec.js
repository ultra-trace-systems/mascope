import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { h, reactive } from 'vue'
import { createPinia, setActivePinia } from 'pinia'

import { ledgerTierChips } from '@/lib/ledger/useLedgerFilters.js'
import { ledgerListing } from '@/lib/referenceListings'

// The one ledger component, mounted as each scope configures it: the shared
// columns in sentence case, the scope's own columns where its slots put them,
// the tier strip, and the states that stand where the table would.

vi.mock('@/stores', () => ({
  useApp: () => ({ ui: { help: { docUrl: (path = '') => `/docs/${path}` } } })
}))
vi.mock('@/lib/base', () => ({
  BaseTabbedPanel: {
    props: ['label', 'icon', 'pt'],
    template: '<div class="panel" :data-label="label"><slot name="menu" /><slot /></div>'
  },
  BaseEmptyState: { props: ['title'], template: '<div class="empty-state">{{ title }}</div>' },
  BaseLoadError: {
    props: ['error', 'fallback', 'onRetry'],
    template: '<div class="load-error">{{ fallback }}</div>'
  },
  BaseProvisionalMark: { template: '<span class="provisional" />' },
  BaseTierTag: {
    props: ['tier', 'tooltip'],
    template: '<span class="tier-tag" :data-tier="tier" />'
  },
  BaseVerdictBadge: {
    props: { record: Object, inherited: Boolean, conflict: Object, compact: Boolean },
    template:
      '<span class="verdict-badge" :data-verdict="record?.verdict" ' +
      ':data-inherited="String(Boolean(inherited))" :data-conflict="conflict?.verdict" />'
  }
}))

// The table renders each column's header and a body cell per row, in the
// order the columns are declared - the order PrimeVue draws them in.
const DataTable = {
  name: 'DataTable',
  props: {
    value: Array,
    sortField: String,
    sortOrder: Number,
    lazy: Boolean,
    removableSort: Boolean,
    scrollable: Boolean,
    scrollHeight: String,
    virtualScrollerOptions: Object,
    filterDisplay: String,
    size: String
  },
  template: '<div class="table"><slot /><div class="empty"><slot name="empty" /></div></div>'
}
const Column = {
  name: 'Column',
  props: { field: String, sortField: String, sortable: Boolean, selectionMode: String },
  inject: ['ledgerRows'],
  template:
    '<div class="column" :data-field="field ?? selectionMode ?? \'\'">' +
    '<div class="head"><slot name="header" /></div>' +
    '<div v-for="(row, i) in ledgerRows.list" :key="i" class="cell" :data-row="row.id">' +
    '<slot name="body" :data="row" /></div></div>'
}
const STUBS = {
  DataTable,
  Column,
  Button: { template: '<button class="cog" />' },
  Popover: { template: '<div class="popover"><slot /></div>' },
  ProgressSpinner: { template: '<div class="spinner" />' },
  Select: true
}

const { default: AssignmentLedger } = await import('@/lib/ledger/AssignmentLedger.vue')

const SCOPES = {
  sample: {
    fields: {
      mz: 'sample_peak_mz',
      intensity: 'sample_peak_intensity',
      formula: 'assigned_formula',
      listing: 'listing',
      level: 'level',
      levelSort: 'levelRank'
    },
    rows: [
      { id: 'a', sample_peak_mz: 100.05, sample_peak_intensity: 500, assigned_formula: 'C2H6' },
      { id: 'b', sample_peak_mz: 200.1, sample_peak_intensity: null, assigned_formula: null }
    ],
    // The scope's own columns, where its slots put them.
    slots: {
      'lead-column': () => h(Column, { field: 'chart' }),
      'middle-columns': () => h(Column, { field: 'mech' }),
      'end-columns': () => h(Column, { field: 'queueRank' })
    },
    columns: [
      'chart',
      'sample_peak_mz',
      'sample_peak_intensity',
      'assigned_formula',
      'listing',
      'mech',
      'tierRank',
      'queueRank',
      'verdictRank'
    ]
  },
  batch: {
    fields: {
      mz: 'mz',
      intensity: 'max_intensity',
      formula: 'consensus_formula',
      listing: 'listingName',
      level: 'member_level',
      levelSort: 'memberLevelRank'
    },
    rows: [
      { id: 'a', mz: 100.05, max_intensity: 500, consensus_formula: 'C2H6' },
      { id: 'b', mz: 200.1, max_intensity: null, consensus_formula: null, parentId: 'a' }
    ],
    slots: {
      'lead-column': () => h(Column, { selectionMode: 'multiple' }),
      'end-columns': () => [
        h(Column, { field: 'n_present' }),
        h(Column, { field: 'continuityRank' })
      ]
    },
    columns: [
      'multiple',
      'mz',
      'max_intensity',
      'consensus_formula',
      'listingName',
      'tierRank',
      'n_present',
      'continuityRank',
      'verdictRank'
    ]
  }
}

function mountLedger(scope, { props = {}, slots = {} } = {}) {
  const config = SCOPES[scope]
  const rows = props.rows ?? config.rows
  return mount(AssignmentLedger, {
    props: {
      scope,
      label: scope === 'sample' ? 'Assignments' : 'Batch peaks',
      count: `${rows.length} rows`,
      rows,
      rowHeight: 35,
      fields: config.fields,
      chips: ledgerTierChips({ assigned: 1, candidate: 1 }),
      activeTiers: reactive(new Set()),
      verdictCell: (row) => ({
        record: row.verdict ?? null,
        overlay: row.overlay ?? null,
        conflict: row.conflict ?? null,
        stale: Boolean(row.stale),
        disabled: Boolean(row.history),
        label: 'Verdict'
      }),
      verdictHelp: { title: 'Verdicts' },
      ...props
    },
    slots: {
      ...config.slots,
      formula: ({ data }) => h('span', { class: 'formula' }, data[config.fields.formula] ?? ''),
      ...slots
    },
    global: {
      stubs: STUBS,
      directives: { tooltip: {}, help: {} },
      provide: { ledgerRows: { list: rows } }
    }
  })
}

const fields = (wrapper) =>
  wrapper.findAll('.column').map((column) => column.attributes('data-field'))
const column = (wrapper, field) =>
  wrapper.findAll('.column').find((col) => col.attributes('data-field') === field)
const cell = (wrapper, field, id) => column(wrapper, field).find(`.cell[data-row="${id}"]`)

beforeEach(() => {
  localStorage.clear()
  setActivePinia(createPinia())
})

describe.each(Object.keys(SCOPES))('AssignmentLedger, %s scope', (scope) => {
  it("renders the scope's columns, the shared ones in place", () => {
    const wrapper = mountLedger(scope)
    expect(fields(wrapper)).toEqual(SCOPES[scope].columns)
  })

  it('heads the shared columns in sentence case', () => {
    const wrapper = mountLedger(scope)
    const heads = wrapper.findAll('.column .head').map((head) => head.text())
    expect(heads).toEqual(expect.arrayContaining(['m/z', 'Intensity', 'Formula', 'Listed', 'Tier']))
  })

  it('adds the engine tier and the level only where a row carries one', () => {
    expect(fields(mountLedger(scope))).not.toContain('engineTierRank')
    const wrapper = mountLedger(scope, { props: { hasEngineTiers: true, hasLevels: true } })
    expect(fields(wrapper)).toContain('engineTierRank')
    expect(fields(wrapper)).toContain(SCOPES[scope].fields.level)
    const level = wrapper
      .findAllComponents(Column)
      .find((c) => c.props('field') === SCOPES[scope].fields.level)
    expect(level.props('sortField')).toBe(SCOPES[scope].fields.levelSort)
  })

  it('sorts the verdict column on its rank', () => {
    const wrapper = mountLedger(scope)
    const verdict = wrapper
      .findAllComponents(Column)
      .find((c) => c.props('field') === 'verdictRank')
    expect(verdict.props('sortable')).toBe(true)
  })

  it('leaves an empty intensity blank', () => {
    const wrapper = mountLedger(scope)
    expect(cell(wrapper, SCOPES[scope].fields.intensity, 'b').text()).toBe('')
    expect(cell(wrapper, SCOPES[scope].fields.intensity, 'a').text()).not.toBe('')
  })

  it('counts its rows in the header line', () => {
    const wrapper = mountLedger(scope)
    expect(wrapper.find('[data-testid="ledger-count"]').text()).toBe('2 rows')
  })

  it('draws a chip per tier and per role, and says which are on', async () => {
    const active = reactive(new Set(['assigned', 'candidate']))
    const wrapper = mountLedger(scope, { props: { activeTiers: active } })
    const chips = wrapper.findAll('.tier-stat')
    expect(chips.map((chip) => chip.text())).toEqual([
      '1 assigned',
      '1 candidate',
      '0 below',
      '0 unassigned',
      '0 reagent',
      '0 artifact'
    ])
    expect(chips[0].classes()).toContain('active')
    expect(chips[1].classes()).toContain('active')
    expect(chips[2].classes()).toContain('dim')
    await chips[3].trigger('click')
    expect(wrapper.emitted('toggle-tier')).toEqual([['unassigned']])
  })

  it('opens the verdict form from the verdict cell', async () => {
    const wrapper = mountLedger(scope)
    await cell(wrapper, 'verdictRank', 'a').find('button').trigger('click')
    const [event, row] = wrapper.emitted('open-verdict')[0]
    expect(event).toBeInstanceOf(Event)
    expect(row.id).toBe('a')
  })

  it('stands one empty state in for a table with no rows', () => {
    const wrapper = mountLedger(scope, {
      props: { rows: [], emptyTitle: 'No rows match these filters' }
    })
    expect(wrapper.find('.empty .empty-state').text()).toBe('No rows match these filters')
  })

  // Inline, so the strip and its view menu stay usable while only the rows
  // are unavailable.
  it('reports a load error inline, the strip kept', () => {
    const wrapper = mountLedger(scope, {
      props: { error: new Error('boom'), errorFallback: 'Could not load.' }
    })
    expect(wrapper.find('.load-error').text()).toBe('Could not load.')
    expect(wrapper.find('.table').exists()).toBe(false)
    expect(wrapper.findAll('.tier-stat')).toHaveLength(6)
  })

  it('shows a spinner in place of the table while the rows load', () => {
    const wrapper = mountLedger(scope, { props: { pending: true } })
    expect(wrapper.find('.spinner').exists()).toBe(true)
    expect(wrapper.find('.table').exists()).toBe(false)
  })

  it("gives the scope's own state the place of the whole ledger", () => {
    const wrapper = mountLedger(scope, {
      slots: { state: () => h('div', { class: 'no-runs' }, 'No assignment runs') }
    })
    expect(wrapper.find('.no-runs').exists()).toBe(true)
    expect(wrapper.find('.tier-strip').exists()).toBe(false)
    expect(wrapper.find('.table').exists()).toBe(false)
  })
})

describe('AssignmentLedger, the shared cells', () => {
  it('shows no engine tier on an isotopologue line where the scope says so', () => {
    const rows = [
      { id: 'a', mz: 100, consensus_formula: 'C2H6', engine_tier: 'assigned' },
      { id: 'b', mz: 101, engine_tier: 'candidate', parentId: 'a' }
    ]
    const wrapper = mountLedger('batch', {
      props: { rows, hasEngineTiers: true, engineTierOnLines: false }
    })
    expect(cell(wrapper, 'engineTierRank', 'a').find('.tier-tag').exists()).toBe(true)
    expect(cell(wrapper, 'engineTierRank', 'b').find('.tier-tag').exists()).toBe(false)
    const both = mountLedger('batch', { props: { rows, hasEngineTiers: true } })
    expect(cell(both, 'engineTierRank', 'b').find('.tier-tag').exists()).toBe(true)
  })

  it('shows no listing on an isotopologue line, and a blank cell where no list names the row', () => {
    const listing = ledgerListing({ name: 'ethane', source: 'list' })
    const rows = [
      { id: 'a', mz: 100, listing },
      { id: 'b', mz: 101, listing, parentId: 'a' },
      { id: 'c', mz: 102 }
    ]
    const wrapper = mountLedger('batch', { props: { rows } })
    expect(cell(wrapper, 'listingName', 'a').find('[data-testid="listed-as"]').exists()).toBe(true)
    expect(cell(wrapper, 'listingName', 'b').text()).toBe('')
    expect(cell(wrapper, 'listingName', 'c').text()).toBe('')
  })

  it("hands the verdict cell the scope's record and label", () => {
    const rows = [
      { id: 'a', mz: 100, verdict: { verdict: 'confirmed' } },
      { id: 'b', mz: 101 }
    ]
    const wrapper = mountLedger('batch', { props: { rows } })
    expect(
      cell(wrapper, 'verdictRank', 'a').find('.verdict-badge').attributes('data-verdict')
    ).toBe('confirmed')
    expect(cell(wrapper, 'verdictRank', 'b').find('.verdict-badge').exists()).toBe(false)
    expect(cell(wrapper, 'verdictRank', 'b').find('button').classes()).toContain('unjudged')
  })

  // A row's own verdict, a batch-level one reaching it (borrowed), a stale
  // one outlined, and history read-only: one cell for both scopes.
  it('draws the verdict a row carries, borrowed or its own, and its state', async () => {
    const rows = [
      { id: 'own', mz: 100, verdict: { verdict: 'rejected' }, conflict: { verdict: 'confirmed' } },
      { id: 'borrowed', mz: 101, overlay: { verdict: 'confirmed' } },
      { id: 'stale', mz: 102, verdict: { verdict: 'unsure' }, stale: true },
      { id: 'history', mz: 103, history: true }
    ]
    const wrapper = mountLedger('sample', { props: { rows } })
    const button = (id) => cell(wrapper, 'verdictRank', id).find('button')
    const badge = (id) => cell(wrapper, 'verdictRank', id).find('.verdict-badge')

    expect(badge('own').attributes('data-inherited')).toBe('false')
    expect(badge('own').attributes('data-conflict')).toBe('confirmed')
    expect(badge('borrowed').attributes('data-inherited')).toBe('true')
    expect(badge('borrowed').attributes('data-verdict')).toBe('confirmed')
    expect(button('borrowed').classes()).not.toContain('unjudged')
    expect(button('stale').classes()).toContain('stale')
    expect(button('own').classes()).not.toContain('stale')
    expect(button('history').attributes('disabled')).toBeDefined()
    expect(button('own').attributes('disabled')).toBeUndefined()
  })
})
