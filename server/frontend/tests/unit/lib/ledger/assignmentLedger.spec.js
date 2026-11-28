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
    props: { tier: String, tooltip: String, outlined: Boolean, cap: Object },
    template:
      '<span class="tier-tag" :data-tier="tier" :data-outlined="String(outlined)" ' +
      ':data-cap="cap?.label" />'
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
    size: String,
    rowClass: Function
  },
  template: '<div class="table"><slot /><div class="empty"><slot name="empty" /></div></div>'
}
const Column = {
  name: 'Column',
  props: {
    field: String,
    sortField: String,
    sortable: Boolean,
    selectionMode: String,
    headerClass: String,
    bodyClass: String
  },
  inject: ['ledgerRows'],
  template:
    '<div class="column" :data-field="field ?? selectionMode ?? \'\'">' +
    '<div class="head" :class="headerClass"><slot name="header" /></div>' +
    '<div v-for="(row, i) in ledgerRows.list" :key="i" class="cell" :class="bodyClass" ' +
    ':data-row="row.id"><slot name="body" :data="row" /></div></div>'
}
const STUBS = {
  DataTable,
  Column,
  Button: { template: '<button class="cog" />' },
  Popover: { template: '<div class="popover"><slot /></div>' },
  ProgressSpinner: { template: '<div class="spinner" />' },
  Select: true,
  IconField: { template: '<span class="icon-field"><slot /></span>' },
  InputIcon: { template: '<span class="input-icon"><slot /></span>' },
  InputText: {
    props: ['modelValue'],
    emits: ['update:modelValue'],
    template:
      '<input class="search-input" :value="modelValue" ' +
      '@input="$emit(\'update:modelValue\', $event.target.value)" />'
  },
  ToggleSwitch: {
    props: ['modelValue', 'inputId'],
    emits: ['update:modelValue'],
    template:
      '<button class="toggle" :id="inputId" ' +
      '@click="$emit(\'update:modelValue\', !modelValue)">{{ modelValue }}</button>'
  }
}

const { default: AssignmentLedger } = await import('@/lib/ledger/AssignmentLedger.vue')
const { shownAt } = await import('./narrowRules.js')

const SCOPES = {
  sample: {
    fields: {
      mz: 'sample_peak_mz',
      intensity: 'sample_peak_intensity',
      formula: 'assigned_formula',
      ion: 'mech',
      level: 'level',
      levelSort: 'levelRank'
    },
    rows: [
      {
        id: 'a',
        sample_peak_mz: 100.05,
        sample_peak_intensity: 500,
        assigned_formula: 'C2H6',
        mech: '[M+H]+'
      },
      { id: 'b', sample_peak_mz: 200.1, sample_peak_intensity: null, assigned_formula: null }
    ],
    // A family's line: the sample's rows mark it `isChild`.
    line: { isChild: true },
    // The scope's own columns, where its slots put them.
    slots: {
      'lead-column': () => h(Column, { field: 'chart' }),
      'end-columns': () => h(Column, { field: 'queueRank' })
    },
    // Plot, m/z, Compound, Ion, Intensity, Tier, the scope's, the verdict.
    columns: [
      'chart',
      'sample_peak_mz',
      'assigned_formula',
      'mech',
      'sample_peak_intensity',
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
      ion: 'consensus_ion_formula',
      level: 'member_level',
      levelSort: 'memberLevelRank'
    },
    rows: [
      {
        id: 'a',
        mz: 100.05,
        max_intensity: 500,
        consensus_formula: 'C2H6',
        consensus_ion_formula: 'C2H7+'
      },
      { id: 'b', mz: 200.1, max_intensity: null, consensus_formula: null }
    ],
    // A family's line: the batch's rows carry the family they fold under.
    line: { parentId: 'a' },
    slots: {
      'lead-column': () => h(Column, { selectionMode: 'multiple' }),
      'end-columns': () => [h(Column, { field: 'n_present' }), h(Column, { field: 'acrossRank' })]
    },
    columns: [
      'multiple',
      'mz',
      'consensus_formula',
      'consensus_ion_formula',
      'max_intensity',
      'tierRank',
      'n_present',
      'acrossRank',
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
      fields: config.fields,
      chips: ledgerTierChips({ assigned: 1, candidate: 3, reagent: 2 }),
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

// A row of the scope, its shared fields named as the scope names them.
const row = (scope, { formula = null, intensity = null, mz = 100.05 } = {}) => {
  const f = SCOPES[scope].fields
  return { [f.mz]: mz, [f.intensity]: intensity, [f.formula]: formula }
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
    expect(heads).toEqual(expect.arrayContaining(['m/z', 'Compound', 'Ion', 'Intensity', 'Tier']))
    expect(heads).not.toContain('Formula')
    expect(heads).not.toContain('Listed')
  })

  // The engine's own tier is a chip in the Tier cell, never a column of its
  // own (owner's decision of 2026-11-22); the level is a column where any row
  // has one, the first column dropped at a narrow width (its chips then show
  // in the Tier cell).
  it('adds the level only where a row carries one, and no engine column', () => {
    expect(fields(mountLedger(scope))).not.toContain(SCOPES[scope].fields.level)
    const wrapper = mountLedger(scope, { props: { engineReadings: true, hasLevels: true } })
    expect(fields(wrapper)).not.toContain('engineTierRank')
    expect(fields(wrapper)).toContain(SCOPES[scope].fields.level)
    const level = wrapper
      .findAllComponents(Column)
      .find((c) => c.props('field') === SCOPES[scope].fields.level)
    expect(level.props('sortField')).toBe(SCOPES[scope].fields.levelSort)
    expect(level.props('headerClass')).toBe('col-level')
    expect(level.props('bodyClass')).toBe('col-level')
  })

  // The level stays at every width (the owner, 2026-11-24): where the Level
  // column drops, its chips show in the Tier cell, and nowhere twice. The
  // narrow rules are applied to the mounted ledger, as a browser would at
  // that ledger width.
  describe('the level at a width', () => {
    const levelRows = () => [
      { id: 'a', ...row(scope, { formula: 'C2H6', intensity: 500 }), engine_tier: 'assigned' }
    ]
    const mountLevels = (props = {}) =>
      mountLedger(scope, {
        props: { rows: levelRows(), hasLevels: true, ...props },
        slots: {
          tier: () => h('span', { class: 'own-tier' }),
          level: () => [
            h('span', { class: 'own-level' }),
            props.engineReadings ? h('span', { class: 'engine-level' }) : null
          ]
        }
      })
    // Where a chip shows at `width`: the Tier cell, the Level column, both.
    const placesAt = (wrapper, selector, width) => {
      const f = SCOPES[scope].fields
      return [
        ['tier', cell(wrapper, 'tierRank', 'a')],
        ['level', cell(wrapper, f.level, 'a')]
      ]
        .filter(([, at]) => at.findAll(selector).some((chip) => shownAt(chip.element, width)))
        .map(([place]) => place)
    }

    it.each([
      [600, ['tier']],
      [800, ['tier']],
      [900, ['tier']],
      [901, ['level']],
      [1000, ['level']]
    ])('at %i px shows the level in %j', (width, places) => {
      const wrapper = mountLevels()
      expect(placesAt(wrapper, '.own-level', width)).toEqual(places)
      // The tier stays at every width, in its own cell.
      expect(placesAt(wrapper, '.own-tier', width)).toEqual(['tier'])
    })

    // Show > Engine readings: the engine's level goes where the engine's tier
    // does at a narrow width, beside its own in the Tier cell.
    it.each([
      [600, ['tier']],
      [1000, ['level']]
    ])("at %i px shows the engine's level in %j", (width, places) => {
      const wrapper = mountLevels({ engineReadings: true })
      expect(placesAt(wrapper, '.engine-level', width)).toEqual(places)
      expect(
        cell(wrapper, 'tierRank', 'a')
          .findAll('[data-testid="engine-tier-chip"]')
          .some((chip) => shownAt(chip.element, width))
      ).toBe(true)
    })

    it('puts no level in the Tier cell where no row has one', () => {
      const wrapper = mountLevels({ hasLevels: false })
      expect(cell(wrapper, 'tierRank', 'a').find('.tier-level').exists()).toBe(false)
      expect(cell(wrapper, 'tierRank', 'a').find('.own-level').exists()).toBe(false)
    })
  })

  // With the column dropped, its header - the filter menu and the sort - goes
  // with it, so Show carries both, writing the same filter and sort.
  describe("the level's filter and sort in Show", () => {
    const f = SCOPES[scope].fields
    const filtersWith = (value) => ({
      [f.level]: { operator: 'and', constraints: [{ value, matchMode: 'equals' }] }
    })
    const menu = (wrapper) => wrapper.find('.level-menu')

    it('is offered only where the ledger has levels', () => {
      expect(menu(mountLedger(scope)).exists()).toBe(false)
      expect(menu(mountLedger(scope, { props: { hasLevels: true } })).exists()).toBe(true)
    })

    it("writes the Level column's own filter, and reads it back", async () => {
      const updates = []
      const wrapper = mountLedger(scope, {
        props: {
          hasLevels: true,
          filters: filtersWith(null),
          'onUpdate:filters': (next) => updates.push(next)
        }
      })
      await menu(wrapper).find('.level-filter [data-value="4a"]').trigger('click')
      expect(updates.at(-1)[f.level].constraints[0]).toEqual({ value: '4a', matchMode: 'equals' })

      await wrapper.setProps({ filters: filtersWith('4a') })
      const chip = menu(wrapper).find('.level-filter [data-value="4a"]')
      expect(chip.attributes('aria-pressed')).toBe('true')
      // Clicking the chip that is on clears the filter.
      await chip.trigger('click')
      expect(updates.at(-1)[f.level].constraints[0].value).toBe(null)
    })

    it("sorts on the level's rank, and turns the sort off", async () => {
      const wrapper = mountLedger(scope, { props: { hasLevels: true } })
      await menu(wrapper).find('.level-sort [data-value="1"]').trigger('click')
      expect(wrapper.emitted('update:sortField').at(-1)).toEqual([f.levelSort])
      expect(wrapper.emitted('update:sortOrder').at(-1)).toEqual([1])

      await wrapper.setProps({ sortField: f.levelSort, sortOrder: -1 })
      const weakest = menu(wrapper).find('.level-sort [data-value="-1"]')
      expect(weakest.attributes('aria-pressed')).toBe('true')
      await weakest.trigger('click')
      expect(wrapper.emitted('update:sortField').at(-1)).toEqual([null])
      expect(wrapper.emitted('update:sortOrder').at(-1)).toEqual([null])
    })

    it('shows no sort as on while another column sorts', () => {
      const wrapper = mountLedger(scope, {
        props: { hasLevels: true, sortField: f.mz, sortOrder: 1 }
      })
      expect(menu(wrapper).findAll('.level-sort [aria-pressed="true"]')).toHaveLength(0)
    })
  })

  it('shows the compact intensity and its bar, the exact number on hover', () => {
    const rows = [
      { id: 'a', ...row(scope, { intensity: 19 }) },
      { id: 'b', ...row(scope, { intensity: 8310 }) },
      { id: 'c', ...row(scope, { intensity: 1_200_000 }) }
    ]
    const wrapper = mountLedger(scope, { props: { rows } })
    const text = (id) => cell(wrapper, SCOPES[scope].fields.intensity, id).find('.intensity').text()
    expect(['a', 'b', 'c'].map(text)).toEqual(['19', '8.31k', '1.20M'])
    const width = (id) =>
      cell(wrapper, SCOPES[scope].fields.intensity, id).find('.intensity-fill').element.style.width
    // On a log scale over the rows: the faintest a sliver, the brightest whole.
    expect(width('a')).toBe('4.0%')
    expect(width('c')).toBe('100.0%')
    // 8,310 sits 55 % of the way up the decades from 19 to 1.2M.
    expect(width('b')).toBe('56.8%')
  })

  // A bar keeps its length as filters narrow the rows: it is drawn over the
  // scope's every row when the scope gives them.
  it('draws the bars over the intensities the scope gives', () => {
    const rows = [{ id: 'a', ...row(scope, { intensity: 1000 }) }]
    const wrapper = mountLedger(scope, { props: { rows, intensities: [10, 1000, 100000] } })
    expect(
      cell(wrapper, SCOPES[scope].fields.intensity, 'a').find('.intensity-fill').element.style.width
    ).toBe('52.0%')
  })

  it('opens the peak from its m/z', async () => {
    const wrapper = mountLedger(scope, { props: { mzAction: 'Open it' } })
    const mz = cell(wrapper, SCOPES[scope].fields.mz, 'a').find('[data-testid="ledger-mz"]')
    expect(mz.attributes('aria-label')).toBe('100.0500: Open it')
    await mz.trigger('click')
    expect(wrapper.emitted('open-mz')[0][0].id).toBe('a')
  })

  it("shows the Compound cell's formula, and a listed name under it only where one is", () => {
    const listing = ledgerListing({ name: 'ethane', source: 'list' })
    const rows = [
      { id: 'a', ...row(scope, { formula: 'C2H6' }), listing },
      { id: 'c', ...row(scope, { formula: 'C3H8' }) }
    ]
    const wrapper = mountLedger(scope, { props: { rows } })
    const compound = (id) =>
      cell(wrapper, SCOPES[scope].fields.formula, id).find('[data-testid="compound-cell"]')
    expect(compound('a').find('.formula').text()).toBe('C2H6')
    expect(compound('a').find('[data-testid="listed-as"]').text()).toContain('ethane')
    expect(compound('c').find('.formula').text()).toBe('C3H8')
    expect(compound('c').find('[data-testid="listed-as"]').exists()).toBe(false)
  })

  it("draws the scope's Ion cell, or the ion field where the scope draws none", () => {
    const own = mountLedger(scope)
    expect(cell(own, SCOPES[scope].fields.ion, 'a').text()).toBe(
      SCOPES[scope].rows[0][SCOPES[scope].fields.ion]
    )
    const drawn = mountLedger(scope, {
      slots: { ion: ({ data }) => h('span', { class: 'pill' }, `x${data.id}`) }
    })
    expect(cell(drawn, SCOPES[scope].fields.ion, 'a').find('.pill').text()).toBe('xa')
  })

  // A line shows its label, its m/z and its intensity, and nothing it would
  // only repeat of its family's row.
  it('draws a line as a sub-row: no ion, tier, level, verdict or action', () => {
    // Both rows carry an ion, so the line's empty Ion cell is the line's own.
    const ion = { [SCOPES[scope].fields.ion]: '[M+H]+' }
    const rows = [
      {
        id: 'a',
        ...row(scope, { formula: 'C2H6', intensity: 500 }),
        ...ion,
        engine_tier: 'assigned'
      },
      {
        id: 'l',
        ...row(scope, { formula: 'C2H6', intensity: 20 }),
        ...ion,
        engine_tier: 'assigned',
        ...SCOPES[scope].line
      }
    ]
    const wrapper = mountLedger(scope, {
      props: { rows, hasLevels: true, engineReadings: true },
      slots: {
        'line-label': () => h('span', { class: 'label' }, '[13C]'),
        tier: () => h('span', { class: 'own-tier' }),
        level: () => h('span', { class: 'own-level' }),
        actions: () => h('button', { class: 'act' })
      }
    })
    const f = SCOPES[scope].fields
    expect(cell(wrapper, f.formula, 'l').find('.label').text()).toBe('[13C]')
    expect(cell(wrapper, f.formula, 'l').find('[data-testid="compound-cell"]').exists()).toBe(false)
    expect(cell(wrapper, f.mz, 'l').text()).not.toBe('')
    expect(cell(wrapper, f.intensity, 'l').find('.intensity').text()).toBe('20')
    expect(cell(wrapper, f.ion, 'l').text()).toBe('')
    expect(cell(wrapper, f.ion, 'a').text()).toBe('[M+H]+')
    for (const field of ['tierRank', f.level, 'verdictRank']) {
      expect(cell(wrapper, field, 'l').element.children).toHaveLength(0)
      expect(cell(wrapper, field, 'a').element.children.length).toBeGreaterThan(0)
    }
    expect(cell(wrapper, '', 'l').find('.act').exists()).toBe(false)
    expect(cell(wrapper, '', 'a').find('.act').exists()).toBe(true)
    // And the table marks it a sub-row, beside the scope's own class.
    const table = wrapper.findComponent({ name: 'DataTable' })
    expect(table.props('rowClass')(rows[1])).toContain('ledger-line')
    expect(table.props('rowClass')(rows[0])).not.toContain('ledger-line')
  })

  it("keeps the scope's row class beside the sub-row's", () => {
    const wrapper = mountLedger(scope, {
      props: { rowClass: (r) => (r.id === 'a' ? 'stale-peak' : null) }
    })
    const rowClass = wrapper.findComponent({ name: 'DataTable' }).props('rowClass')
    expect(rowClass({ id: 'a' })).toEqual(['stale-peak'])
    expect(rowClass({ id: 'a', ...SCOPES[scope].line })).toEqual(['stale-peak', 'ledger-line'])
  })

  it("draws a family's caret with its line count, and says when it is open", async () => {
    const wrapper = mountLedger(scope, {
      props: { familySize: (r) => (r.id === 'a' ? 3 : 0), familyOpen: () => true }
    })
    const caret = cell(wrapper, SCOPES[scope].fields.formula, 'a').find(
      '[data-testid="family-caret"]'
    )
    expect(caret.text()).toBe('3')
    expect(caret.attributes('aria-expanded')).toBe('true')
    expect(caret.attributes('aria-label')).toBe('Fold 3 isotopologue lines')
    expect(
      cell(wrapper, SCOPES[scope].fields.formula, 'b').find('[data-testid="family-caret"]').exists()
    ).toBe(false)
    await caret.trigger('click')
    expect(wrapper.emitted('toggle-family')[0][0].id).toBe('a')
  })

  it("draws the engine's tier as a second, outlined chip only with Show's engine readings", () => {
    const rows = [{ id: 'a', ...row(scope, { formula: 'C2H6' }), engine_tier: 'candidate' }]
    const tier = { tier: () => h('span', { class: 'own-tier' }) }
    const off = mountLedger(scope, { props: { rows }, slots: tier })
    expect(cell(off, 'tierRank', 'a').find('[data-testid="engine-tier-chip"]').exists()).toBe(false)
    const on = mountLedger(scope, {
      props: { rows, engineReadings: true, engineTierTooltip: () => 'the engine says so' },
      slots: tier
    })
    const chip = cell(on, 'tierRank', 'a').find('[data-testid="engine-tier-chip"]')
    expect(chip.attributes('data-tier')).toBe('candidate')
    expect(chip.attributes('data-outlined')).toBe('true')
    expect(cell(on, 'tierRank', 'a').find('.own-tier').exists()).toBe(true)
  })

  it("hands the scope's tier cell the cap mark it reads", () => {
    const wrapper = mountLedger(scope, {
      props: { tierCap: (r) => (r.id === 'a' ? { label: 'one peak only' } : null) },
      slots: { tier: ({ cap }) => h('span', { class: 'own-tier' }, cap?.label ?? '') }
    })
    expect(cell(wrapper, 'tierRank', 'a').text()).toBe('one peak only')
    expect(cell(wrapper, 'tierRank', 'b').text()).toBe('')
  })

  it('adds the actions column only where the scope has actions', () => {
    expect(fields(mountLedger(scope))).not.toContain('')
    const wrapper = mountLedger(scope, { slots: { actions: () => h('button', { class: 'act' }) } })
    expect(fields(wrapper).at(-1)).toBe('')
  })

  it("leaves room for the scope's control after the count in the header line", () => {
    const wrapper = mountLedger(scope, {
      slots: { 'header-end': () => h('button', { class: 'control' }) }
    })
    expect(wrapper.find('.header-end .control').exists()).toBe(true)
    expect(mountLedger(scope).find('.header-end').exists()).toBe(false)
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

  it('draws a chip per bucket with rows, the roles apart, and says which are on', async () => {
    const active = reactive(new Set(['assigned', 'candidate']))
    const wrapper = mountLedger(scope, { props: { activeTiers: active } })
    const chips = wrapper.findAll('.tier-stat')
    expect(chips.map((chip) => chip.text())).toEqual(['1 assigned', '3 candidate', '2 reagent'])
    // The roles sit in a group of their own after the tiers.
    expect(wrapper.findAll('[data-testid="role-chips"] .tier-stat').map((c) => c.text())).toEqual([
      '2 reagent'
    ])
    expect(chips[0].classes()).toContain('active')
    expect(chips[1].classes()).toContain('active')
    expect(chips[2].classes()).toContain('dim')
    await chips[2].trigger('click')
    expect(wrapper.emitted('toggle-tier')).toEqual([['reagent']])
  })

  // The bar draws the tiers' shares, in their order; the roles are no
  // confidence and stay out of it.
  it('draws the tier bar over the chips, the tiers only, by share', () => {
    const wrapper = mountLedger(scope)
    const segments = wrapper.findAll('[data-testid="tier-bar"] .tier-segment')
    expect(segments.map((segment) => segment.classes()[1])).toEqual(['assigned', 'candidate'])
    expect(segments.map((segment) => segment.attributes('data-share'))).toEqual([
      '0.2500',
      '0.7500'
    ])
    expect(wrapper.find('[data-testid="tier-bar"]').attributes('aria-label')).toBe(
      'Tiers: 25 % assigned, 75 % candidate'
    )
    // Above the chips.
    const strip = wrapper.find('.tier-strip').element
    expect(strip.firstElementChild.dataset.testid).toBe('tier-bar')
  })

  it('draws no bar while nothing is tiered', () => {
    const wrapper = mountLedger(scope, { props: { chips: ledgerTierChips({ reagent: 2 }) } })
    expect(wrapper.find('[data-testid="tier-bar"]').exists()).toBe(false)
  })

  it('hands the search box its query and reports what is typed', async () => {
    const wrapper = mountLedger(scope, { props: { search: 'C6H' } })
    const input = wrapper.find('[data-testid="ledger-search"]')
    expect(input.element.value).toBe('C6H')
    await input.setValue('181.07')
    expect(wrapper.emitted('update:search')).toEqual([['181.07']])
  })

  // Show takes the cog's place; the overflow holds the scope's actions and the
  // listing switch both scopes share.
  it("puts Show and the overflow at the bar's end, the listing switch in the overflow", async () => {
    const wrapper = mountLedger(scope, {
      slots: {
        'view-menu': () => h('div', { class: 'show-item' }),
        overflow: () => h('button', { class: 'export-item' })
      }
    })
    const popovers = wrapper.findAll('.filter-bar .popover')
    expect(popovers).toHaveLength(2)
    expect(popovers[0].find('.show-item').exists()).toBe(true)
    expect(popovers[1].find('.export-item').exists()).toBe(true)
    const listing = popovers[1].find(`#${scope}-listing-full`)
    expect(listing.exists()).toBe(true)
    await listing.trigger('click')
    expect(JSON.parse(localStorage.getItem('mascope.browser.match.ledgerView')).listingFull).toBe(
      true
    )
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
    expect(wrapper.findAll('.tier-stat')).toHaveLength(3)
    expect(wrapper.find('[data-testid="ledger-search"]').exists()).toBe(true)
    expect(wrapper.find('.cog').exists()).toBe(true)
  })

  // The scope may still hold the last good load's rows: their count, in the
  // header or on a chip, would read as this load's.
  it('shows no count and no chip counts while the rows failed to load', async () => {
    const wrapper = mountLedger(scope, { props: { error: new Error('boom') } })
    expect(wrapper.find('[data-testid="ledger-count"]').exists()).toBe(false)
    expect(wrapper.findAll('.tier-stat').map((chip) => chip.text())).toEqual([
      'assigned',
      'candidate',
      'reagent'
    ])
    // Nor the bar, whose shares are those counts.
    expect(wrapper.find('[data-testid="tier-bar"]').exists()).toBe(false)
    await wrapper.setProps({ error: null })
    expect(wrapper.find('[data-testid="ledger-count"]').text()).toBe('2 rows')
    expect(wrapper.findAll('.tier-stat')[0].text()).toBe('1 assigned')
  })

  // A scope that draws no strip for an empty ledger (the batch scope) still
  // gets it on a failed first load, so the chips and the view menu stay.
  it('draws the strip on a failed load where the scope would leave it out', () => {
    expect(
      mountLedger(scope, { props: { strip: false } })
        .find('.tier-strip')
        .exists()
    ).toBe(false)
    const wrapper = mountLedger(scope, { props: { strip: false, error: new Error('boom') } })
    expect(wrapper.find('.tier-strip').exists()).toBe(true)
    expect(wrapper.find('.filter-bar').exists()).toBe(true)
    expect(wrapper.find('.cog').exists()).toBe(true)
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

// The lens and Group by (MAS-1069): the bar over the narrowed rows, its label
// column, and the rows that stand for no peak of the scope.
describe('AssignmentLedger, groups', () => {
  const LENS = { title: 'CH2 series from C5H8O4, 4 batch peaks', column: 'Step', plot: true }
  const rows = [
    { id: 'a', mz: 100.05, consensus_formula: 'C5H8O4', lensLabel: 'root', verdict: null },
    {
      id: 'gap',
      ledgerKind: 'gap',
      lensLabel: '+1 CH2',
      pseudoFormula: 'C6H10O4',
      pseudoNote: 'no batch peak'
    },
    {
      id: 'absent',
      ledgerKind: 'absent',
      lensLabel: '+2 CH2',
      pseudoMz: 222.1,
      pseudoFormula: 'C7H12O4',
      pseudoIon: 'C7H12NO7-',
      pseudoNote: 'not in this sample'
    }
  ]

  it('names the group in a bar, labels each row and offers to plot it', async () => {
    const wrapper = mountLedger('batch', { props: { rows, lens: LENS } })
    expect(wrapper.find('[data-testid="lens-title"]').text()).toBe(LENS.title)
    // The label column comes after the scope's Plot column.
    expect(fields(wrapper).slice(0, 2)).toEqual(['multiple', 'lensLabel'])
    expect(column(wrapper, 'lensLabel').find('.head').text()).toBe('Step')
    expect(cell(wrapper, 'lensLabel', 'gap').text()).toBe('+1 CH2')

    await wrapper.find('[data-testid="lens-plot"]').trigger('click')
    await wrapper.find('[data-testid="lens-close"]').trigger('click')
    expect(wrapper.emitted('plot-lens')).toHaveLength(1)
    expect(wrapper.emitted('close-lens')).toHaveLength(1)
  })

  it('draws neither the bar nor the column without a lens, nor Plot where it is not offered', () => {
    const wrapper = mountLedger('batch', { props: { rows: rows.slice(0, 1) } })
    expect(wrapper.find('[data-testid="lens-bar"]').exists()).toBe(false)
    expect(fields(wrapper)).not.toContain('lensLabel')
    const sample = mountLedger('sample', { props: { rows, lens: { ...LENS, plot: false } } })
    expect(sample.find('[data-testid="lens-plot"]').exists()).toBe(false)
  })

  it('closes on Escape, but not while a menu over it takes the key', async () => {
    const wrapper = mountLedger('batch', { props: { rows, lens: LENS }, attachTo: document.body })
    const popover = document.createElement('div')
    popover.className = 'p-popover'
    const inside = document.createElement('button')
    popover.appendChild(inside)
    document.body.appendChild(popover)
    inside.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    expect(wrapper.emitted('close-lens')).toBeUndefined()
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(wrapper.emitted('close-lens')).toHaveLength(1)
    popover.remove()

    // Closed, it listens no more.
    const removed = vi.spyOn(window, 'removeEventListener')
    await wrapper.setProps({ lens: null })
    expect(removed).toHaveBeenCalledWith('keydown', expect.any(Function))
    removed.mockRestore()
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(wrapper.emitted('close-lens')).toHaveLength(1)
    wrapper.unmount()
  })

  it('draws a gap and an absent member greyed, with what each is and no tier or verdict', () => {
    const wrapper = mountLedger('batch', { props: { rows, lens: LENS } })
    const rowClass = wrapper.findComponent(DataTable).props('rowClass')
    expect(rowClass(rows[1])).toEqual(['ledger-pseudo ledger-gap'])
    expect(rowClass(rows[0])).toEqual([])

    const gap = cell(wrapper, 'consensus_formula', 'gap')
    const parts = (el) => el.findAll('span').map((span) => span.text())
    expect(parts(gap.find('[data-testid="pseudo-gap"]'))).toEqual(['C6H10O4', 'no batch peak'])
    const absent = cell(wrapper, 'consensus_formula', 'absent')
    expect(parts(absent.find('[data-testid="pseudo-absent"]'))).toEqual([
      'C7H12O4',
      'not in this sample'
    ])
    // The absent member's m/z is no link: there is no row to open.
    expect(cell(wrapper, 'mz', 'absent').find('[data-testid="ledger-mz"]').exists()).toBe(false)
    expect(cell(wrapper, 'mz', 'absent').text()).toBe('222.1000')
    expect(cell(wrapper, 'consensus_ion_formula', 'absent').text()).toBe('C7H12NO7-')
    for (const id of ['gap', 'absent']) {
      expect(cell(wrapper, 'tierRank', id).find('.tier-tag').exists()).toBe(false)
      expect(cell(wrapper, 'verdictRank', id).find('button').exists()).toBe(false)
    }
    expect(cell(wrapper, 'mz', 'a').find('[data-testid="ledger-mz"]').exists()).toBe(true)
  })

  it('gives a row that stands for no peak no tier, level or action', () => {
    const wrapper = mountLedger('batch', {
      props: { rows, lens: LENS, hasLevels: true },
      slots: {
        actions: () => h('button', { class: 'act' }),
        tier: () => h('span', { class: 'tier-slot' })
      }
    })
    expect(cell(wrapper, 'tierRank', 'a').find('.tier-slot').exists()).toBe(true)
    expect(cell(wrapper, 'tierRank', 'gap').find('.tier-slot').exists()).toBe(false)
    const actions = wrapper.findAll('.column').at(-1)
    expect(actions.find('.cell[data-row="a"] .act').exists()).toBe(true)
    expect(actions.find('.cell[data-row="gap"] .act').exists()).toBe(false)
    expect(cell(wrapper, 'member_level', 'a').find('.level-cell').exists()).toBe(true)
    expect(cell(wrapper, 'member_level', 'absent').find('.level-cell').exists()).toBe(false)
  })

  it('draws a group line: its name and its count, folded by a button', async () => {
    const line = {
      id: 'g',
      ledgerKind: 'group',
      groupKey: 'k',
      groupName: 'C6H10O4',
      groupCount: '2 batch peaks',
      collapsed: false
    }
    const wrapper = mountLedger('batch', { props: { rows: [line] } })
    const button = cell(wrapper, 'consensus_formula', 'g').find('[data-testid="group-line"]')
    expect(button.element.tagName).toBe('BUTTON')
    expect(button.find('.group-name').text()).toBe('C6H10O4')
    expect(button.find('.group-count').text()).toBe('2 batch peaks')
    expect(button.attributes('aria-label')).toBe('Fold C6H10O4, 2 batch peaks')
    expect(button.attributes('aria-expanded')).toBe('true')
    await button.trigger('click')
    expect(wrapper.emitted('toggle-group')).toEqual([[line]])
    // Enter and Space stop at the button: the table would read them as a
    // selection of the row.
    const outer = vi.fn()
    wrapper.element.addEventListener('keydown', outer)
    await button.trigger('keydown', { key: 'Enter' })
    await button.trigger('keydown', { key: ' ' })
    expect(outer).not.toHaveBeenCalled()
  })

  // A group has no tier of its own: its header's Tier, Level and verdict
  // cells stay empty, in either scope, whatever its members carry.
  it.each(['sample', 'batch'])('draws no tier, level or verdict on a group line (%s)', (scope) => {
    const f = SCOPES[scope].fields
    const line = {
      id: 'g',
      ledgerKind: 'group',
      groupName: 'C6H10O4',
      groupCount: '2 peaks',
      // What an older pane put on the line: drawn nowhere now.
      groupTier: 'assigned',
      groupRole: 'reagent',
      engine_tier: 'assigned',
      verdict: { verdict: 'confirmed' },
      collapsed: false
    }
    const member = { id: 'a', [f.mz]: 100.05, [f.formula]: 'C6H10O4' }
    const wrapper = mountLedger(scope, {
      props: { rows: [line, member], engineReadings: true, hasLevels: true },
      slots: {
        tier: () => h('span', { class: 'tier-slot' }),
        level: () => h('span', { class: 'level-slot' })
      }
    })
    const tier = cell(wrapper, 'tierRank', 'g')
    expect(tier.find('.tier-tag').exists()).toBe(false)
    expect(tier.find('.tier-slot').exists()).toBe(false)
    expect(tier.find('.level-slot').exists()).toBe(false)
    expect(cell(wrapper, f.level, 'g').find('.level-slot').exists()).toBe(false)
    expect(cell(wrapper, 'verdictRank', 'g').find('button').exists()).toBe(false)
    // Its member draws all three.
    expect(cell(wrapper, 'tierRank', 'a').find('.tier-slot').exists()).toBe(true)
    expect(cell(wrapper, f.level, 'a').find('.level-slot').exists()).toBe(true)
    expect(cell(wrapper, 'verdictRank', 'a').find('button').exists()).toBe(true)
  })

  // The rows under a group line carry its guide, up to the next group line;
  // the header itself, a row before any group line and a lens's rows do not.
  it('marks every row under a group line, its lines and references included', () => {
    const rows = [
      { id: 'g1', ledgerKind: 'group', groupName: 'C6H10O4', groupCount: '2 batch peaks' },
      { id: 'a', mz: 100 },
      { id: 'a-line', mz: 101, parentId: 'a' },
      { id: 'ref', ledgerKind: 'reference', pseudoNote: 'listed under C5H8O4' },
      { id: 'g2', ledgerKind: 'group', groupName: 'Ungrouped', groupCount: '1 batch peak' },
      { id: 'b', mz: 200 }
    ]
    const wrapper = mountLedger('batch', { props: { rows } })
    const rowClass = wrapper.findComponent(DataTable).props('rowClass')
    const marked = rows.filter((r) => rowClass(r).includes('ledger-in-group')).map((r) => r.id)
    expect(marked).toEqual(['a', 'a-line', 'ref', 'b'])
    // A line under a group keeps its own quiet class beside the guide.
    expect(rowClass(rows[2])).toEqual(['ledger-line', 'ledger-in-group'])
    expect(rowClass(rows[0])).toEqual(['ledger-pseudo ledger-group'])
  })

  it('marks no row where no group line is drawn, as under the lens', () => {
    const rows = [
      { id: 'a', mz: 100, lensLabel: 'root' },
      { id: 'gap', ledgerKind: 'gap', lensLabel: '+1 CH2', pseudoNote: 'no batch peak' }
    ]
    const wrapper = mountLedger('batch', {
      props: { rows, lens: { title: 'A series', column: 'Step' } }
    })
    const rowClass = wrapper.findComponent(DataTable).props('rowClass')
    expect(rows.some((r) => rowClass(r).includes('ledger-in-group'))).toBe(false)
  })
})
