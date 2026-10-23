import { describe, it, expect, vi, afterEach } from 'vitest'
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

// The confirmation a create asks for, and the collection calls a save makes.
const { confirmRequire, collectionApi } = vi.hoisted(() => ({
  confirmRequire: vi.fn(),
  collectionApi: { create: vi.fn(), update: vi.fn() }
}))
vi.mock('primevue/useconfirm', () => ({ useConfirm: () => ({ require: confirmRequire }) }))

// The paste context drags in the whole base barrel. Its stub keeps wrapping
// the panel's changes table; a test pastes by emitting its `validated` event
// with what the real parser makes of the clipboard.
vi.mock('@/lib/base', () => ({
  BaseClipboardContext: { name: 'BaseClipboardContext', template: '<div><slot /></div>' }
}))

const noPt = () => ({})
// The stored compounds the "All compounds" source lists; empty unless a test
// fills it.
const { compoundList } = vi.hoisted(() => ({ compoundList: [] }))
// The collection an update opens on; none unless a test sets one.
const { opened } = vi.hoisted(() => ({ opened: { collection: null } }))
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
          get detailed() {
            return opened.collection
          },
          list: [],
          read: vi.fn(),
          create: collectionApi.create,
          update: collectionApi.update,
          delete: vi.fn()
        },
        compound: { list: compoundList }
      }
    }
  })
}))

import DialogTargetCollectionOp from '@/lib/dialogs/DialogTargetCollectionOp.vue'
import { parseCompoundPaste } from '@/lib/chem'

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

const ETHANOL = {
  target_compound_id: 'tc-ethanol',
  target_compound_formula: 'C2H6O',
  target_compound_name: 'Ethanol',
  inchikey: 'LFQSCWFLJHTTHZ-UHFFFAOYSA-N'
}
const DIMETHYL_ETHER_KEY = 'LCGLNKUTAGEVQW-UHFFFAOYSA-N'
// A stored compound of ethanol's formula with no structure: most stored
// compounds have none.
const KEYLESS_C2H6O = {
  target_compound_id: 'tc-keyless',
  target_compound_formula: 'C2H6O',
  target_compound_name: 'Unknown alcohol'
}

const METHANE = {
  cas: '74-82-8',
  inchikey: 'VNWKTOKETHGBQD-UHFFFAOYSA-N',
  inchi: 'InChI=1S/CH4/h1H4',
  smiles: 'C'
}

// The changes table: the one whose dataKey is a function of the row.
const changesTable = (wrapper) =>
  wrapper
    .findAllComponents({ name: 'DataTable' })
    .find((table) => typeof table.props('dataKey') === 'function')

const saveButton = (wrapper) =>
  wrapper
    .findAllComponents({ name: 'Button' })
    .find((button) => button.attributes('label') === 'Save')

// Saves a new collection and returns what it sends to the API.
async function saveCreated(wrapper) {
  saveButton(wrapper).vm.$emit('click')
  await nextTick()
  const [[{ accept }]] = confirmRequire.mock.calls
  accept()
  const [[sent]] = collectionApi.create.mock.calls
  return sent
}

describe('DialogTargetCollectionOp manual compound input', () => {
  afterEach(() => {
    compoundList.splice(0)
    confirmRequire.mockReset()
    collectionApi.create.mockReset()
  })

  const field = (wrapper, id) =>
    wrapper.findAllComponents({ name: 'InputText' }).find((input) => input.attributes('id') === id)

  async function openOnInput(action = 'create') {
    const wrapper = mount(DialogTargetCollectionOp, {
      props: { action: null },
      global: { plugins: [PrimeVue], stubs, directives: { tooltip: {} } }
    })
    await wrapper.setProps({ action })
    await flushPromises()
    wrapper
      .findAllComponents({ name: 'SelectButton' })
      .find((c) => (c.props('options') ?? []).some((o) => o.value === 'input'))
      .vm.$emit('update:modelValue', 'input')
    await nextTick()
    return wrapper
  }

  const type = async (wrapper, id, value) => {
    field(wrapper, id).vm.$emit('update:modelValue', value)
    await nextTick()
  }

  const typeStructure = async (wrapper, structure) => {
    for (const [name, value] of Object.entries(structure)) {
      await type(wrapper, `create-compound-${name}`, value)
    }
  }

  const addButton = (wrapper) =>
    wrapper
      .findAllComponents({ name: 'Button' })
      .find((button) => /compound$/.test(button.attributes('label') ?? ''))

  const structureGroup = (wrapper) =>
    wrapper
      .findAllComponents({ name: 'Panel' })
      .find((panel) => panel.props('header') === 'Structure')

  it('has a collapsed Structure group with CAS, InChIKey, InChI and SMILES', async () => {
    const wrapper = await openOnInput()

    expect(structureGroup(wrapper).props('collapsed')).toBe(true)
    for (const id of [
      'add-compound-formula',
      'add-compound-name',
      'create-compound-cas',
      'create-compound-inchikey',
      'create-compound-inchi',
      'create-compound-smiles'
    ]) {
      expect(field(wrapper, id), id).toBeDefined()
    }
  })

  it('creates the compound from its formula and name', async () => {
    const wrapper = await openOnInput()
    await type(wrapper, 'add-compound-formula', 'CH4')
    await type(wrapper, 'add-compound-name', 'Methane')

    addButton(wrapper).vm.$emit('click')
    await nextTick()

    expect(changesTable(wrapper).props('value')).toEqual([
      expect.objectContaining({
        target_compound_formula: 'CH4',
        target_compound_name: 'Methane',
        status: '1 create'
      })
    ])
  })

  it('finds a stored compound by the CAS number entered', async () => {
    compoundList.push({
      target_compound_id: 'tc1',
      target_compound_formula: 'CH4',
      target_compound_name: 'Methane',
      cas_number: METHANE.cas
    })
    const wrapper = await openOnInput()
    await type(wrapper, 'add-compound-formula', 'CH3D')
    expect(addButton(wrapper).attributes('label')).toBe('Create compound')

    await typeStructure(wrapper, { cas: METHANE.cas })
    expect(addButton(wrapper).attributes('label')).toBe('Add compound')
    expect(addButton(wrapper).attributes('severity')).toBe('info')
  })

  it('will not add a CAS number already in the selection twice', async () => {
    const wrapper = await openOnInput()
    await type(wrapper, 'add-compound-formula', 'CH4')
    await typeStructure(wrapper, { cas: METHANE.cas })
    addButton(wrapper).vm.$emit('click')
    await nextTick()

    await type(wrapper, 'add-compound-formula', 'CH3D')
    await typeStructure(wrapper, { cas: METHANE.cas })
    expect(addButton(wrapper).attributes('disabled')).toBe('true')
  })

  it('sends the structure entered with the created compound', async () => {
    const wrapper = await openOnInput()
    await type(wrapper, 'add-compound-formula', 'CH4')
    await type(wrapper, 'add-compound-name', 'Methane')
    await typeStructure(wrapper, METHANE)
    addButton(wrapper).vm.$emit('click')
    await nextTick()
    await type(wrapper, 'add-compound-formula', 'C2H6')
    await typeStructure(wrapper, { inchikey: 'OTMSDBZUPAUEDD-UHFFFAOYSA-N', smiles: '  ' })
    addButton(wrapper).vm.$emit('click')
    await nextTick()

    const { target_compounds_create } = await saveCreated(wrapper)
    expect(target_compounds_create).toEqual([
      {
        target_compound_formula: 'CH4',
        target_compound_name: 'Methane',
        cas_number: METHANE.cas,
        inchikey: METHANE.inchikey,
        inchi: METHANE.inchi,
        smiles: METHANE.smiles
      },
      {
        target_compound_formula: 'C2H6',
        target_compound_name: '',
        cas_number: '',
        inchikey: 'OTMSDBZUPAUEDD-UHFFFAOYSA-N',
        inchi: null,
        // a blank input is no SMILES: the API refuses an empty one
        smiles: null
      }
    ])
  })

  it('empties the form and collapses its Structure group after adding', async () => {
    const wrapper = await openOnInput()
    structureGroup(wrapper).vm.$emit('update:collapsed', false)
    await type(wrapper, 'add-compound-formula', 'CH4')
    await typeStructure(wrapper, METHANE)

    addButton(wrapper).vm.$emit('click')
    await nextTick()

    expect(structureGroup(wrapper).props('collapsed')).toBe(true)
    for (const name of Object.keys(METHANE)) {
      expect(field(wrapper, `create-compound-${name}`).props('modelValue'), name).toBe('')
    }
  })

  it('offers to create the isomer of a stored compound, and to add the same structure', async () => {
    compoundList.push({ ...ETHANOL })
    const wrapper = await openOnInput()
    await type(wrapper, 'add-compound-formula', 'C2H6O')
    await typeStructure(wrapper, { inchikey: DIMETHYL_ETHER_KEY })
    expect(addButton(wrapper).attributes('label')).toBe('Create compound')

    await typeStructure(wrapper, { inchikey: ETHANOL.inchikey })
    expect(addButton(wrapper).attributes('label')).toBe('Add compound')
    expect(addButton(wrapper).attributes('severity')).toBe('info')
  })

  it('adds the stored compound of the InChIKey, not a keyless one listed before it', async () => {
    compoundList.push({ ...KEYLESS_C2H6O }, { ...ETHANOL })
    const wrapper = await openOnInput()
    await type(wrapper, 'add-compound-formula', 'C2H6O')
    await typeStructure(wrapper, { inchikey: ETHANOL.inchikey })
    expect(addButton(wrapper).attributes('label')).toBe('Add compound')
    addButton(wrapper).vm.$emit('click')
    await nextTick()

    const { target_compound_ids, target_compounds_create } = await saveCreated(wrapper)
    expect(target_compound_ids).toEqual([ETHANOL.target_compound_id])
    expect(target_compounds_create).toEqual([])
  })

  it('adds the stored compound of the InChIKey when a keyless one is already selected', async () => {
    compoundList.push({ ...KEYLESS_C2H6O }, { ...ETHANOL })
    const wrapper = await openOnInput()
    await type(wrapper, 'add-compound-formula', 'C2H6O')
    addButton(wrapper).vm.$emit('click')
    await nextTick()

    await type(wrapper, 'add-compound-formula', 'C2H6O')
    await typeStructure(wrapper, { inchikey: ETHANOL.inchikey })
    expect(addButton(wrapper).attributes('disabled')).toBe('false')
    addButton(wrapper).vm.$emit('click')
    await nextTick()

    const { target_compound_ids, target_compounds_create } = await saveCreated(wrapper)
    expect(target_compound_ids).toEqual([
      KEYLESS_C2H6O.target_compound_id,
      ETHANOL.target_compound_id
    ])
    expect(target_compounds_create).toEqual([])
  })

  it('will not add a selected compound missing from the stored list twice', async () => {
    // the collection's compound is not among the stored ones listed
    opened.collection = {
      target_collection_id: 'c1',
      target_collection_name: 'Solvents',
      target_collection_type: 'TARGETS',
      workspace_id: 'w1',
      target_compounds: [{ ...KEYLESS_C2H6O }]
    }
    try {
      const wrapper = await openOnInput('update')
      await type(wrapper, 'add-compound-formula', 'C2H6O')
      expect(addButton(wrapper).attributes('disabled')).toBe('true')
    } finally {
      opened.collection = null
    }
  })

  it('will add an isomer of a compound already in the selection', async () => {
    const wrapper = await openOnInput()
    await type(wrapper, 'add-compound-formula', 'C2H6O')
    await typeStructure(wrapper, { inchikey: ETHANOL.inchikey })
    addButton(wrapper).vm.$emit('click')
    await nextTick()

    await type(wrapper, 'add-compound-formula', 'C2H6O')
    await typeStructure(wrapper, { inchikey: ETHANOL.inchikey })
    expect(addButton(wrapper).attributes('disabled')).toBe('true')
    await typeStructure(wrapper, { inchikey: DIMETHYL_ETHER_KEY })
    expect(addButton(wrapper).attributes('disabled')).toBe('false')
  })

  it.each([
    ['InChIKey', { inchikey: 'VNWKTOKETHGBQD' }, /InChIKey reads like/],
    ['InChI', { inchi: '1S/CH4/h1H4' }, /InChI starts with InChI=/]
  ])('will not add a malformed %s, by the button or by Enter', async (_, structure, tooltip) => {
    const wrapper = await openOnInput()
    await type(wrapper, 'add-compound-formula', 'CH4')
    await typeStructure(wrapper, structure)

    expect(addButton(wrapper).attributes('disabled')).toBe('true')
    expect(
      field(wrapper, `create-compound-${Object.keys(structure)[0]}`).attributes('invalid')
    ).toBe('true')
    window.dispatchEvent(new KeyboardEvent('keydown', { code: 'Enter' }))
    await nextTick()
    expect(changesTable(wrapper)).toBeUndefined()

    // the tooltip says why; the directive stub keeps its binding off the DOM,
    // so read the reason the button is given
    expect(wrapper.vm.$.setupState.addCompoundButtonConfig.tooltip).toMatch(tooltip)
  })
})

describe('DialogTargetCollectionOp spreadsheet paste', () => {
  afterEach(() => {
    compoundList.splice(0)
    confirmRequire.mockReset()
    collectionApi.create.mockReset()
  })

  // The changes table's rows, rendered: the table stub hands each row to the
  // body slot of every column, so the status column's markup is on screen.
  const RowTable = {
    name: 'DataTable',
    props: ['value', 'dataKey', 'selection'],
    provide() {
      return { table: this }
    },
    template: '<div><slot /></div>'
  }
  const RowColumn = {
    name: 'Column',
    props: ['field', 'header'],
    inject: ['table'],
    template:
      '<div><div v-for="(row, i) in table.value ?? []" :key="i" class="cell"><slot name="body" :data="row" /></div></div>'
  }

  async function open(action = 'create') {
    const wrapper = mount(DialogTargetCollectionOp, {
      props: { action: null },
      global: {
        plugins: [PrimeVue],
        stubs: { ...stubs, DataTable: RowTable, Column: RowColumn },
        directives: { tooltip: {} }
      }
    })
    await wrapper.setProps({ action })
    await flushPromises()
    return wrapper
  }

  const paste = async (wrapper, text) => {
    wrapper
      .findComponent({ name: 'BaseClipboardContext' })
      .vm.$emit('validated', { data: parseCompoundPaste(text) })
    await nextTick()
  }

  const methaneRow = `Methane\tCH4\t${METHANE.cas}\t${METHANE.inchikey}\t${METHANE.inchi}\t${METHANE.smiles}`

  it('sends the pasted structure with each created compound, a blank cell as none', async () => {
    const wrapper = await open()
    await paste(wrapper, `${methaneRow}\nWater\tH2O\t\t\t\tO`)

    const { target_compounds_create } = await saveCreated(wrapper)
    expect(target_compounds_create).toEqual([
      {
        target_compound_name: 'Methane',
        target_compound_formula: 'CH4',
        cas_number: METHANE.cas,
        inchikey: METHANE.inchikey,
        inchi: METHANE.inchi,
        smiles: METHANE.smiles
      },
      {
        target_compound_name: 'Water',
        target_compound_formula: 'H2O',
        cas_number: '',
        inchikey: null,
        inchi: null,
        smiles: 'O'
      }
    ])
  })

  it('does not apply a pasted structure to a stored compound, and says so on its row', async () => {
    compoundList.push(
      {
        target_compound_id: 'tc1',
        target_compound_formula: 'CH4',
        target_compound_name: 'Methane'
      },
      { target_compound_id: 'tc2', target_compound_formula: 'H2O', target_compound_name: 'Water' }
    )
    const wrapper = await open()
    await paste(wrapper, `${methaneRow}\nWater\tH2O\t\t\t\t\nEthane\tC2H6\t\t\t\tCC`)

    const rows = Object.fromEntries(
      changesTable(wrapper)
        .props('value')
        .map((row) => [row.target_compound_formula, row])
    )
    expect(rows.CH4).toMatchObject({ status: '2 add', structureNotApplied: true })
    // a reused row without a structure, and a created one, have nothing to say
    expect(rows.H2O).toMatchObject({
      status: '2 add',
      structureNotApplied: false,
      structureMatched: false
    })
    expect(rows.C2H6).toMatchObject({ status: '1 create', structureNotApplied: false })
    expect(wrapper.findAll('.structure-not-applied')).toHaveLength(1)
    expect(wrapper.findAll('.structure-matched')).toHaveLength(0)

    // nothing of the structure is sent for the stored compound
    const sent = await saveCreated(wrapper)
    expect(sent.target_compound_ids).toEqual(['tc1', 'tc2'])
    expect(sent.target_compounds_create.map((c) => c.target_compound_formula)).toEqual(['C2H6'])
  })

  it('says so on a kept row too', async () => {
    const methane = {
      target_compound_id: 'tc1',
      target_compound_formula: 'CH4',
      target_compound_name: 'Methane'
    }
    compoundList.push(methane)
    opened.collection = {
      target_collection_id: 'c1',
      target_collection_name: 'Gases',
      target_collection_type: 'TARGETS',
      workspace_id: 'w1',
      target_compounds: [methane]
    }
    try {
      const wrapper = await open('update')
      await paste(wrapper, methaneRow)

      expect(changesTable(wrapper).props('value')).toEqual([
        expect.objectContaining({ status: '3 keep', structureNotApplied: true })
      ])
      expect(wrapper.findAll('.structure-not-applied')).toHaveLength(1)
    } finally {
      opened.collection = null
    }
  })

  it('forgets which rows had a structure not applied when the dialog opens again', async () => {
    compoundList.push({
      target_compound_id: 'tc1',
      target_compound_formula: 'CH4',
      target_compound_name: 'Methane'
    })
    const wrapper = await open()
    await paste(wrapper, methaneRow)
    expect(wrapper.findAll('.structure-not-applied')).toHaveLength(1)

    await wrapper.setProps({ action: null })
    await wrapper.setProps({ action: 'create' })
    await flushPromises()
    await paste(wrapper, 'Methane\tCH4')

    expect(changesTable(wrapper).props('value')).toEqual([
      expect.objectContaining({ target_compound_id: 'tc1', structureNotApplied: false })
    ])
  })

  // Rows by name: isomers share a formula.
  const rowsByName = (wrapper) =>
    Object.fromEntries(
      changesTable(wrapper)
        .props('value')
        .map((row) => [row.target_compound_name, row])
    )

  it('creates both isomers of one paste, each said to be an isomer', async () => {
    const wrapper = await open()
    await paste(
      wrapper,
      `Ethanol\tC2H6O\t\t${ETHANOL.inchikey}\nDimethyl ether\tC2H6O\t\t${DIMETHYL_ETHER_KEY}`
    )

    const rows = rowsByName(wrapper)
    expect(rows.Ethanol).toMatchObject({ status: '1 create', isomerOf: 'Dimethyl ether' })
    expect(rows['Dimethyl ether']).toMatchObject({ status: '1 create', isomerOf: 'Ethanol' })
    expect(wrapper.findAll('.structure-isomer')).toHaveLength(2)

    const { target_compounds_create } = await saveCreated(wrapper)
    expect(target_compounds_create.map((c) => c.inchikey)).toEqual([
      ETHANOL.inchikey,
      DIMETHYL_ETHER_KEY
    ])
  })

  it('keys two unnamed isomers apart in the changes table', async () => {
    const wrapper = await open()
    await paste(wrapper, `\tC2H6O\t\t${ETHANOL.inchikey}\n\tC2H6O\t\t${DIMETHYL_ETHER_KEY}`)

    const table = changesTable(wrapper)
    const keys = table.props('value').map(table.props('dataKey'))
    expect(new Set(keys).size).toBe(2)
  })

  it('removes one created isomer and keeps the other', async () => {
    const wrapper = await open()
    await paste(
      wrapper,
      `Ethanol\tC2H6O\t\t${ETHANOL.inchikey}\nDimethyl ether\tC2H6O\t\t${DIMETHYL_ETHER_KEY}`
    )
    wrapper.vm.$.setupState.remove(rowsByName(wrapper)['Dimethyl ether'])
    await nextTick()

    expect(changesTable(wrapper).props('value')).toEqual([
      expect.objectContaining({ target_compound_name: 'Ethanol', isomerOf: null })
    ])
  })

  it('creates the isomer of a stored compound instead of reusing it', async () => {
    compoundList.push({ ...ETHANOL })
    const wrapper = await open()
    await paste(wrapper, `Dimethyl ether\tC2H6O\t\t${DIMETHYL_ETHER_KEY}`)

    expect(changesTable(wrapper).props('value')).toEqual([
      expect.objectContaining({
        target_compound_name: 'Dimethyl ether',
        status: '1 create',
        isomerOf: 'Ethanol',
        structureNotApplied: false
      })
    ])
    expect(wrapper.findAll('.structure-isomer')).toHaveLength(1)
    expect(wrapper.findAll('.structure-not-applied')).toHaveLength(0)

    const sent = await saveCreated(wrapper)
    expect(sent.target_compound_ids).toEqual([])
    expect(sent.target_compounds_create).toEqual([
      expect.objectContaining({
        target_compound_name: 'Dimethyl ether',
        inchikey: DIMETHYL_ETHER_KEY
      })
    ])
  })

  it('reuses a stored compound of the same InChIKey, and says it matched', async () => {
    compoundList.push({ ...ETHANOL })
    const wrapper = await open()
    await paste(wrapper, `Ethanol\tC2H6O\t\t${ETHANOL.inchikey}\tInChI=1S/C2H6O/c1-2-3/h3H,2H2,1H3`)

    expect(changesTable(wrapper).props('value')).toEqual([
      expect.objectContaining({
        target_compound_id: ETHANOL.target_compound_id,
        status: '2 add',
        structureMatched: true,
        structureNotApplied: false
      })
    ])
    expect(wrapper.findAll('.structure-matched')).toHaveLength(1)
    expect(wrapper.findAll('.structure-not-applied')).toHaveLength(0)
  })

  it('reuses the stored compound of the InChIKey, not a keyless one listed before it', async () => {
    compoundList.push({ ...KEYLESS_C2H6O }, { ...ETHANOL })
    const wrapper = await open()
    await paste(wrapper, `Ethanol\tC2H6O\t\t${ETHANOL.inchikey}`)

    expect(changesTable(wrapper).props('value')).toEqual([
      expect.objectContaining({
        target_compound_id: ETHANOL.target_compound_id,
        status: '2 add',
        structureMatched: true,
        structureNotApplied: false
      })
    ])
  })

  it('adds the stored compound of the InChIKey when a keyless one is already selected', async () => {
    compoundList.push({ ...KEYLESS_C2H6O }, { ...ETHANOL })
    const wrapper = await open()
    await paste(wrapper, 'Unknown alcohol\tC2H6O')
    await paste(wrapper, `Ethanol\tC2H6O\t\t${ETHANOL.inchikey}`)

    expect(changesTable(wrapper).props('value')).toEqual([
      expect.objectContaining({
        target_compound_id: KEYLESS_C2H6O.target_compound_id,
        status: '2 add'
      }),
      expect.objectContaining({ target_compound_id: ETHANOL.target_compound_id, status: '2 add' })
    ])
  })

  it('removes the stored compound of the InChIKey and keeps the keyless one', async () => {
    compoundList.push({ ...KEYLESS_C2H6O }, { ...ETHANOL })
    const wrapper = await open()
    await paste(wrapper, `Unknown alcohol\tC2H6O\nEthanol\tC2H6O\t\t${ETHANOL.inchikey}`)
    const ethanol = changesTable(wrapper)
      .props('value')
      .find((row) => row.target_compound_id === ETHANOL.target_compound_id)
    wrapper.vm.$.setupState.remove(ethanol)
    await nextTick()

    expect(changesTable(wrapper).props('value')).toEqual([
      expect.objectContaining({
        target_compound_id: KEYLESS_C2H6O.target_compound_id,
        status: '2 add'
      })
    ])
  })

  it('says so on a kept row matched by its InChIKey too', async () => {
    compoundList.push({ ...ETHANOL })
    opened.collection = {
      target_collection_id: 'c1',
      target_collection_name: 'Solvents',
      target_collection_type: 'TARGETS',
      workspace_id: 'w1',
      target_compounds: [{ ...ETHANOL }]
    }
    try {
      const wrapper = await open('update')
      await paste(wrapper, `Ethanol\tC2H6O\t\t${ETHANOL.inchikey}`)

      expect(changesTable(wrapper).props('value')).toEqual([
        expect.objectContaining({ status: '3 keep', structureMatched: true })
      ])
      expect(wrapper.findAll('.structure-matched')).toHaveLength(1)
    } finally {
      opened.collection = null
    }
  })

  it('keeps the formula match for a row without a structure', async () => {
    compoundList.push({ ...ETHANOL })
    const wrapper = await open()
    await paste(wrapper, 'Alcohol\tC2H6O')

    expect(changesTable(wrapper).props('value')).toEqual([
      expect.objectContaining({
        target_compound_id: ETHANOL.target_compound_id,
        status: '2 add',
        structureMatched: false,
        structureNotApplied: false
      })
    ])
  })

  it('reuses a stored compound of the CAS number whatever the InChIKey', async () => {
    compoundList.push({ ...ETHANOL, cas_number: '64-17-5' })
    const wrapper = await open()
    await paste(wrapper, `Ethanol\tC2H6O\t64-17-5\t${DIMETHYL_ETHER_KEY}`)

    expect(changesTable(wrapper).props('value')).toEqual([
      expect.objectContaining({
        target_compound_id: ETHANOL.target_compound_id,
        status: '2 add',
        structureMatched: false,
        structureNotApplied: true
      })
    ])
  })

  it('forgets which rows matched by structure when the dialog opens again', async () => {
    compoundList.push({ ...ETHANOL })
    const wrapper = await open()
    await paste(wrapper, `Ethanol\tC2H6O\t\t${ETHANOL.inchikey}`)
    expect(wrapper.findAll('.structure-matched')).toHaveLength(1)

    await wrapper.setProps({ action: null })
    await wrapper.setProps({ action: 'create' })
    await flushPromises()
    await paste(wrapper, 'Ethanol\tC2H6O')

    expect(changesTable(wrapper).props('value')).toEqual([
      expect.objectContaining({
        target_compound_id: ETHANOL.target_compound_id,
        structureMatched: false
      })
    ])
  })
})

describe('DialogTargetCollectionOp InChIKey', () => {
  afterEach(() => compoundList.splice(0))

  it('is no column of the compound tables, but can be searched for', async () => {
    compoundList.push(
      {
        target_compound_id: 'tc1',
        target_compound_formula: 'CH4',
        target_compound_name: 'Methane',
        inchikey: 'VNWKTOKETHGBQD-UHFFFAOYSA-N'
      },
      { target_compound_id: 'tc2', target_compound_formula: 'C2H6', target_compound_name: 'Ethane' }
    )
    const wrapper = mount(DialogTargetCollectionOp, {
      props: { action: null },
      global: { plugins: [PrimeVue], stubs, directives: { tooltip: {} } }
    })
    await wrapper.setProps({ action: 'create' })
    await flushPromises()

    const fields = wrapper
      .findAllComponents({ name: 'Column' })
      .map((column) => column.attributes('field'))
    expect(fields).toContain('cas_number')
    expect(fields).not.toContain('inchikey')

    wrapper
      .findAllComponents({ name: 'InputText' })
      .find((input) => input.attributes('placeholder') === 'Search compounds')
      .vm.$emit('update:modelValue', 'vnwktoket')
    await nextTick()

    const [table] = compoundTables(wrapper)
    expect(table.props('value').map(({ target_compound_id }) => target_compound_id)).toEqual([
      'tc1'
    ])
  })
})
