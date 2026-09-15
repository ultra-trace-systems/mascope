<script setup>
import { ref, reactive, computed, watch, watchEffect, onMounted, onBeforeUnmount } from 'vue'

import ConfirmDialog from 'primevue/confirmdialog'
import Dialog from 'primevue/dialog'
import FloatLabel from 'primevue/floatlabel'
import SelectButton from 'primevue/selectbutton'
import Tabs from 'primevue/tabs'
import TabList from 'primevue/tablist'
import Tab from 'primevue/tab'
import TabPanels from 'primevue/tabpanels'
import TabPanel from 'primevue/tabpanel'
import RadioButton from 'primevue/radiobutton'
import InputText from 'primevue/inputtext'
import Panel from 'primevue/panel'
import Button from 'primevue/button'
import Checkbox from 'primevue/checkbox'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Message from 'primevue/message'
import Listbox from 'primevue/listbox'
import Select from 'primevue/select'
import IconField from 'primevue/iconfield'
import InputIcon from 'primevue/inputicon'
import { useConfirm } from 'primevue/useconfirm'

import { api } from '@/api'
import { useApp } from '@/stores'
import { equals } from '@/lib/table'
import { BaseClipboardContext } from '@/lib/base'
import {
  isValidChemicalFormula,
  isSameCompound,
  findExistingCompound,
  parseCompoundPaste,
  validateCompoundPaste
} from '@/lib/chem'
import { clone } from '@/lib/utils'
import { collectionTypes, getAllowedDatasetTypes, getAllowedBatchTypes } from '@/lib/constants'
import { ROLES } from '@/lib/roles'

const confirm = useConfirm()

const app = useApp()
const layer = 'dialog_target_op' // Help-mode layer for dialog

const action = defineModel('action')

// Use detailed collection
const original = computed(() => app.data.target.collection.detailed)

// dialog visibility reactivity
const visible = ref(false)

// Prevents watchers from interfering during initialization
const initializing = ref(false)

// Collection info
const GLOBAL_SENTINEL = '__global__'

// Collection info
const info = reactive({
  id: null,
  name: '',
  desc: '',
  type: 'TARGETS',
  workspace_id: undefined // undefined = use focused workspace; GLOBAL_SENTINEL = global
})

// Compounds state
const compounds = reactive({
  loaded: [], // Loaded from selected collection
  selected: [], // Currently selected compounds
  initial: [], // Initial selection snapshot for change tracking
  created: [] // New compounds created in this dialog
})

// Batches state - spans multiple datasets
const batches = reactive({
  loaded: [], // Batches from current dataset only
  selected: [], // Selected batches across ALL datasets
  initial: [] // Initial selection snapshot for change tracking
})

// UI selection state
const selected = reactive({
  dataset: null,
  source: 'collection',
  collection: 'all-compounds', // for adding compounds
  search: '',
  tab: 'compounds'
})
const add = reactive({
  formula: null,
  name: null,
  cas: null
})
const deleteOrphans = ref(true)

const key = reactive({
  targets: 0
})

const columns = [
  { field: 'target_compound_name', label: 'Name' },
  { field: 'target_compound_formula', label: 'Formula' },
  { field: 'cas_number', label: 'CAS' },
  { field: 'status', label: 'Status' }
]

const title = computed(() => {
  // Define the modal title based on the action
  let title = ''
  switch (action.value) {
    case 'create':
      title = 'Create a new target collection'
      break
    case 'update':
      title = 'Edit target collection'
      break
    case 'update_batches':
      title = 'Manage target collection batches'
      break
    case 'delete':
      title = 'Delete target collection'
      break
  }
  return title
})

const changes = computed(() =>
  [
    ...compounds.selected,
    ...compounds.created,
    ...compounds.initial.filter(
      (initial) =>
        !compounds.selected.find(
          (selected) => selected.target_compound_id == initial.target_compound_id
        )
    )
  ].map((comp) => {
    const selected = compounds.selected.some(
      (selected) => selected.target_compound_id == comp.target_compound_id
    )
    const prexisting = compounds.initial.some(
      (init) => init.target_compound_id == comp.target_compound_id
    )
    const created = compounds.created.some((created) => isSameCompound(created, comp))
    const added = comp.target_compound_id && !prexisting
    const removed = prexisting && !selected
    let status
    if (created) {
      status = '1 create'
    }
    if (selected) {
      if (added) {
        status = '2 add'
      } else if (prexisting) {
        status = '3 keep'
      }
    } else {
      if (removed) {
        status = '4 remove'
      }
    }
    return {
      ...comp,
      status
    }
  })
)

const filteredDatasets = computed(() => {
  if (!info.type) return app.data.dataset.list

  const allowedDatasetTypes = getAllowedDatasetTypes(info.type)
  return app.data.dataset.list
    .filter((dataset) => allowedDatasetTypes.includes(dataset.dataset_type))
    .sort((a, b) => {
      // ensure the current dataset comes first if it's allowed
      if (a.dataset_id == app.data.dataset.focusedId) return -1
      if (b.dataset_id == app.data.dataset.focusedId) return 1
      return 0
    })
    .map((dataset) => ({
      // create labels, demarcating the current one
      ...dataset,
      label:
        dataset.dataset_id === app.data.dataset.focusedId
          ? `${dataset.dataset_name} (current)`
          : dataset.dataset_name
    }))
})

const allowedBatchTypes = computed(() => getAllowedBatchTypes(info.type))

/**
 * Writable boolean tracking whether all currently-loaded batches (for the
 * selected dataset) are present in the global batches.selected.
 *
 * Getter: derives from batches.loaded + batches.selected, so the checkbox
 * stays in sync with individual row toggles, programmatic selection changes
 * (auto-add on type pick, init for update_batches), and dataset switches.
 *
 * Setter:
 *   - true  -> append every loaded batch missing from batches.selected
 *   - false -> remove every loaded batch from batches.selected
 *
 * batches.selected spans all datasets; this only affects the loaded subset
 * (current dataset), leaving selections in other datasets untouched.
 */
const allBatchesSelected = computed({
  get() {
    return (
      batches.loaded.length > 0 &&
      batches.loaded.every((b) =>
        batches.selected.some((s) => s.sample_batch_id === b.sample_batch_id)
      )
    )
  },
  set(select) {
    if (select) {
      const ids = new Set(batches.selected.map((b) => b.sample_batch_id))
      batches.selected = [
        ...batches.selected,
        ...batches.loaded.filter((l) => !ids.has(l.sample_batch_id))
      ]
    } else {
      const ids = new Set(batches.loaded.map((b) => b.sample_batch_id))
      batches.selected = batches.selected.filter((s) => !ids.has(s.sample_batch_id))
    }
  }
})

/**
 * Writable boolean tracking whether all currently-loaded compounds are
 * present in compounds.selected.
 *
 * Getter: derives from compounds.loaded + compounds.selected, so the
 * checkbox stays in sync with row-level toggles, programmatic changes,
 * and source/collection switches.
 *
 * Setter:
 *   - true  -> append every loaded compound missing from compounds.selected
 *   - false -> remove every loaded compound from compounds.selected
 *
 * Compounds are matched by target_compound_formula to match the existing
 * handler logic (covers both saved compounds and pre-save creates).
 */
const allCompoundsSelected = computed({
  get() {
    return (
      compounds.loaded.length > 0 &&
      compounds.loaded.every((c) =>
        compounds.selected.some((s) => s.target_compound_formula === c.target_compound_formula)
      )
    )
  },
  set(select) {
    if (select) {
      const formulas = new Set(compounds.selected.map((c) => c.target_compound_formula))
      compounds.selected = [
        ...compounds.selected,
        ...compounds.loaded.filter((l) => !formulas.has(l.target_compound_formula))
      ]
    } else {
      const formulas = new Set(compounds.loaded.map((c) => c.target_compound_formula))
      compounds.selected = compounds.selected.filter(
        (s) => !formulas.has(s.target_compound_formula)
      )
    }
  }
})

/**
 * Compact badge styling for status Message components in the changes DataTable.
 * Targets PrimeVue Message's internal sections (root/content/text/icon) via pt.
 */

const statusBadgePt = {
  root: { style: 'width: fit-content; margin: 0;' },
  content: { style: 'padding: 0.3rem 0.6rem; align-items: center;' },
  text: { style: 'font-size: 13px; line-height: 1.3;' },
  icon: { style: 'font-size: 13px;' }
}
const removeBadgePt = {
  ...statusBadgePt,
  text: { style: 'font-size: 13px; line-height: 1.3; font-weight: 600;' }
}

function remove(compound) {
  if (compound.status == '1 create') {
    compounds.created = compounds.created.filter((selected) => !isSameCompound(selected, compound))
  } else {
    compounds.selected = compounds.selected.filter(
      (selected) => !isSameCompound(selected, compound)
    )
  }
}
function restore(compound) {
  compounds.selected.push(compound)
}

function loadSpreadsheet({ rows }) {
  let prexisting = []
  rows.forEach((compound) => {
    const record = findExistingCompound(app.data.target.compound.list, compound)
    if (record) {
      prexisting.push(record)
    } else {
      const created = findExistingCompound(compounds.created, compound)
      if (!created) {
        compounds.created.push(compound)
      }
    }
  })
  const index = new Map(
    [...compounds.selected, ...prexisting].map((compound) => [
      compound.target_compound_id ?? compound.target_compound_formula,
      compound
    ])
  )
  const reconciled = prexisting.map((compound) => {
    // Use object from compounds.loaded if available (for proper DataTable selection)
    const loadedCompound = compounds.loaded.find(
      (loaded) => loaded.target_compound_id === compound.target_compound_id
    )

    return (
      loadedCompound ||
      index.get(compound.target_compound_id ?? compound.target_compound_formula) ||
      compound
    )
  })
  const unselected = (added) => !compounds.selected.some((comp) => isSameCompound(comp, added))
  compounds.selected.push(...reconciled.filter(unselected))
  // force DataTable re-render
  key.targets += 1
}

function execute() {
  const common = {
    target_collection_name: info.name,
    target_collection_description: info.desc,
    target_collection_type: info.type,
    workspace_id: info.workspace_id === GLOBAL_SENTINEL ? null : info.workspace_id
  }
  const target_collection_id = original.value?.target_collection_id
  const target_compound_ids = [
    ...new Set(compounds.selected?.map(({ target_compound_id }) => target_compound_id) ?? [])
  ]
  const sample_batch_ids = batches.selected.map(({ sample_batch_id }) => sample_batch_id)
  const target_compounds_create = compounds.created

  switch (action.value) {
    case 'create': {
      const existing = target_compound_ids.length
      const created = target_compounds_create.length
      const totalCompounds = existing + created
      const focusedBatchId = app.data.batch.focusedId
      const focusedIncluded = focusedBatchId
        ? batches.selected.some((b) => b.sample_batch_id === focusedBatchId)
        : false
      const otherCount = batches.selected.length - (focusedIncluded ? 1 : 0)

      let batchSummary
      if (batches.selected.length === 0) {
        batchSummary = focusedBatchId
          ? 'Not assigned to current batch or any other batch.'
          : 'Not assigned to any batch.'
      } else if (!focusedBatchId) {
        batchSummary = `Assigned to ${batches.selected.length} batch${batches.selected.length === 1 ? '' : 'es'}.`
      } else if (focusedIncluded && otherCount === 0) {
        batchSummary = 'Assigned to current batch.'
      } else if (focusedIncluded) {
        batchSummary = `Assigned to current batch and ${otherCount} other${otherCount === 1 ? '' : 's'}.`
      } else {
        batchSummary = `Not assigned to current batch, but assigned to ${otherCount} other${otherCount === 1 ? '' : 's'}.`
      }

      const compoundSummary =
        totalCompounds === 0
          ? 'No compounds yet - assign peaks or update this collection later.'
          : `Total ${totalCompounds} compounds (${created} new, ${existing} existing from other collections).`
      const scopeSummary =
        info.workspace_id === GLOBAL_SENTINEL
          ? 'Scope: Global (shared across all workspaces).'
          : `Scope: ${workspaceScopeOptions.value.find((o) => o.value === info.workspace_id)?.label ?? 'Unknown workspace'}.`
      confirm.require({
        group: 'target-collection-create',
        icon: 'pi pi-info-circle',
        header: `Creating ${info.name} (${info.type})`,
        message: {
          intro: 'Please review collection parameters before creating:',
          items: [scopeSummary, batchSummary, compoundSummary]
        },
        accept: () => {
          app.data.target.collection.create({
            ...common,
            target_compound_ids,
            sample_batch_ids,
            target_compounds_create
          })
          action.value = null
        },
        acceptProps: { label: 'Create', icon: 'pi pi-check' },
        rejectProps: { label: 'Cancel', severity: 'secondary' }
      })

      return
    }
    case 'update': {
      const doUpdate = () => {
        app.data.target.collection.update({
          ...common,
          target_collection_id,
          target_compound_ids,
          target_compounds_create
        })
        action.value = null
      }
      if (info.type === 'CALIBRANTS') {
        const compoundsChanged = !equals(
          compounds.initial,
          compounds.selected,
          'target_compound_id'
        )
        const compoundsCreated = compounds.created.length > 0
        if (compoundsChanged || compoundsCreated) {
          confirm.require({
            icon: 'pi pi-exclamation-triangle',
            header: 'Edit calibration collection',
            message:
              'Editing a calibration collection affects how associated samples are calibrated. ' +
              'Are you sure you want to proceed?',
            accept: doUpdate,
            acceptProps: {
              icon: 'pi pi-check',
              label: 'Save',
              severity: 'warn'
            },
            rejectProps: {
              label: 'Cancel',
              severity: 'secondary'
            }
          })
          return
        }
      }
      doUpdate()
      return
    }
    case 'update_batches': {
      // Only batch associations change here - sending the basic fields
      // (in particular workspace_id) would needlessly trigger scope checks
      app.data.target.collection.update({
        target_collection_id,
        sample_batch_ids
      })
      break
    }
    case 'delete': {
      confirm.require({
        icon: 'pi pi-exclamation-triangle',
        header: 'Delete collection',
        message: `Are you sure you want to delete '${info.name}' target collection?`,
        accept: () => {
          app.data.target.collection.delete({
            collectionId: info.id,
            collectionName: info.name,
            deleteOrphanCompounds: deleteOrphans.value
          })
          action.value = null
        },
        acceptProps: {
          icon: 'pi pi-trash',
          label: 'Delete',
          severity: 'danger'
        },
        rejectProps: {
          icon: 'pi pi-times',
          label: 'Cancel',
          severity: 'secondary'
        }
      })
      break
    }
  }
  action.value = null
}
const executeLabel = computed(() => (action.value == 'delete' ? 'Delete' : 'Save'))
const invalidated = computed(() => {
  switch (action.value) {
    case 'create':
      return !info.name || !info.type || !info.workspace_id

    case 'update': {
      const infoChanged =
        info.name !== original.value.target_collection_name ||
        info.desc !== original.value.target_collection_description ||
        info.type !== original.value.target_collection_type ||
        (info.workspace_id === GLOBAL_SENTINEL ? null : info.workspace_id) !==
          (original.value.workspace_id ?? null)
      const compoundsChanged = !equals(compounds.initial, compounds.selected, 'target_compound_id')
      const compoundsCreated = compounds.created.length > 0 // Check if new compounds were added
      return !(infoChanged || compoundsChanged || compoundsCreated)
    }
    case 'update_batches':
      return equals(batches.initial, batches.selected, 'sample_batch_id')
    default:
      return false
  }
})

const invalidFormula = computed(
  () => add.formula.length > 0 && !isValidChemicalFormula(add.formula)
)

// Workspace scope options for the scope selector
const workspaceScopeOptions = computed(() => {
  const options = app.data.workspace.list
    .filter((w) => !w.is_system)
    .map((w) => ({
      label: w.workspace_name,
      value: w.workspace_id
    }))
  // Only admins+ can create/edit global collections
  if (app.auth.user.role_id >= ROLES.admin) {
    options.unshift({ label: 'Global (all workspaces)', value: GLOBAL_SENTINEL })
  }
  return options
})

// Whether the scope selector should be disabled (only editable on create, not update_batches)
const scopeDisabled = computed(() => action.value === 'update_batches')

// Check for existing compound in db for manual input
const existingInputCompound = computed(() =>
  findExistingCompound(app.data.target.compound.list, {
    target_compound_formula: add.formula,
    target_compound_name: add.name,
    cas_number: add.cas
  })
)

// Check if compound would be duplicate in current selection
const alreadyInSelection = computed(() => {
  if (!add.formula.trim()) return false

  const searchCompound = {
    target_compound_formula: add.formula,
    target_compound_name: add.name,
    cas_number: add.cas
  }

  return (
    compounds.selected.some((comp) => isSameCompound(comp, searchCompound)) ||
    compounds.created.some((comp) => isSameCompound(comp, searchCompound))
  )
})

// Button configuration for manual input
const addCompoundButtonConfig = computed(() => {
  if (!add.formula.trim()) {
    return {
      label: 'Add compound',
      tooltip: 'Enter formula to add compound'
    }
  }
  if (invalidFormula.value) {
    return {
      label: 'Add compound',
      tooltip: 'Invalid chemical formula'
    }
  }
  if (alreadyInSelection.value) {
    return {
      label: 'Add compound',
      tooltip: 'Compound already in selection'
    }
  }
  if (existingInputCompound.value) {
    return {
      label: 'Add compound',
      severity: 'info',
      tooltip: 'Add existing compound'
    }
  }
  return {
    label: 'Create compound',
    severity: 'primary',
    tooltip: 'Create new compound'
  }
})

const addCompoundButtonDisabled = computed(
  () => !add.formula.trim() || invalidFormula.value || alreadyInSelection.value
)

/**
 * Computed property that filters global batch selection to current dataset.
 * This allows batches.selected to track selections across all datasets,
 * while the DataTable only shows/modifies batches from the current dataset.
 */
const datasetBatchSelection = computed({
  get() {
    if (!selected.dataset) return []
    // Filter global selection to only show batches from current dataset
    return batches.selected.filter((batch) => batch.dataset_id === selected.dataset.dataset_id)
  },
  set(newSelection) {
    if (!selected.dataset) return

    // Remove batches from current dataset, keep batches from other datasets
    const otherDatasetBatches = batches.selected.filter(
      (batch) => batch.dataset_id !== selected.dataset.dataset_id
    )

    // Combine with new selection from current dataset
    batches.selected = [...otherDatasetBatches, ...newSelection]
  }
})

/**
 * Load batches for a specific dataset and collection type.
 * Only loads batches for the given dataset - doesn't modify selection state.
 */
async function loadBatches(dataset, collectionType) {
  if (!dataset || !collectionType) {
    batches.loaded = []
    return
  }
  const allowed = getAllowedBatchTypes(collectionType)

  const latest = await api.http.get(`/sample/batches`, {
    params: {
      dataset_id: dataset.dataset_id,
      sample_batch_type: allowed
    },
    use: 'read',
    type: 'load_batches'
  })
  batches.loaded = latest
}

/**
 * Initialize dialog state based on action mode.
 * Sets initializing flag to prevent watchers from interfering.
 */
async function init(mode) {
  if (!mode) {
    return
  }
  initializing.value = true

  // Reset to defaults
  selected.tab = 'compounds'
  selected.collection = 'all-compounds'
  selected.source = 'collection'
  selected.search = ''
  compounds.loaded = []
  compounds.selected = []
  compounds.initial = []
  compounds.created = []
  add.name = ''
  add.formula = ''
  add.cas = ''
  batches.loaded = []
  batches.selected = []

  // Load collection data for update actions
  if (mode.startsWith('update')) {
    info.id = original.value?.target_collection_id
    info.name = original.value?.target_collection_name
    info.desc = original.value?.target_collection_description
    info.type = original.value?.target_collection_type
    info.workspace_id = original.value?.workspace_id ?? GLOBAL_SENTINEL
    compounds.selected = original.value?.target_compounds ?? []
  }

  switch (mode) {
    case 'create': {
      info.id = ''
      info.name = ''
      info.desc = ''
      info.type = null
      info.workspace_id = app.data.workspace.focusedId ?? GLOBAL_SENTINEL
      selected.dataset = app.data.dataset.focused
      break
    }
    case 'update_batches': {
      selected.tab = 'batches'

      // Snapshot all batches where collection is already assigned to (spans datasets).
      // Clone to avoid mutating the original detailed collection record.
      batches.selected = clone(original.value?.sample_batches ?? [])

      // Pick dataset to display in the listbox, prioritizing user's current context:
      //   1. focused dataset, if compatible with collection type (not ACQUISITION,
      //      and its dataset_type is allowed for the collection)
      //   2. dataset owning the focused batch, as fallback when (1) fails - covers
      //      the case where user is in an ACQUISITION dataset editing an ANALYSIS-
      //      only collection
      //   3. null - leaves listbox unselected, user picks manually
      const focusedDataset = app.data.dataset.focused
      const focusedDatasetCompatible =
        focusedDataset?.dataset_type !== 'ACQUISITION' &&
        getAllowedDatasetTypes(info.type).includes(focusedDataset?.dataset_type)

      if (focusedDatasetCompatible) {
        selected.dataset = focusedDataset
      } else {
        const focusedBatchDatasetId = app.data.batch.focused?.dataset_id
        selected.dataset = focusedBatchDatasetId
          ? (app.data.dataset.list.find((d) => d.dataset_id === focusedBatchDatasetId) ?? null)
          : null
      }

      if (selected.dataset) {
        await loadBatches(selected.dataset, info.type)
      }
      break
    }
    case 'delete': {
      info.id = original.value?.target_collection_id
      info.name = original.value?.target_collection_name
      deleteOrphans.value = true
    }
  }
  // Take snapshots for change tracking
  compounds.initial = clone(compounds.selected)
  batches.initial = clone(batches.selected)

  initializing.value = false
}

/**
 * Adds a new compound to the list.
 * This function first checks if the 'Formula' input is not empty,
 * then it adds the compound, and finally, it resets the input fields.
 */
const addCompound = () => {
  if (!add.formula.trim()) {
    return // Prevent adding if formula is empty
  }
  loadSpreadsheet({
    rows: [
      {
        target_compound_formula: add.formula,
        target_compound_name: add.name,
        cas_number: add.cas
      }
    ]
  })
  // Reset input fields
  add.formula = ''
  add.name = ''
  add.cas = ''
}
const onEnter = (event) => {
  if (visible.value && event.code == 'Enter') {
    addCompound()
  }
}
onMounted(() => window.addEventListener('keydown', onEnter))
onBeforeUnmount(() => window.removeEventListener('keydown', onEnter))

// Initialize dialog when action changes
watch(action, async (value) => {
  visible.value = !!value
  if (value) {
    await init(value)
  }
})

// Close dialog when visibility changes to false
watch(visible, (value) => {
  if (!value) {
    app.ui.help.set(null)
    action.value = null
    // Reset all state for fresh data on next open
    selected.dataset = null
    selected.tab = 'compounds'
    selected.collection = 'all-compounds'
    selected.source = 'collection'
    selected.search = ''
    info.type = null
    compounds.loaded = []
    compounds.selected = []
    compounds.initial = []
    compounds.created = []
    batches.loaded = []
    batches.selected = []
    batches.initial = []
    add.name = ''
    add.formula = ''
    add.cas = ''
  } else {
    app.ui.help.set(layer)
  }
})

/**
 * Handle collection type changes.
 * Only active during create/update_batches modes.
 * Resets dataset if it becomes incompatible with new type.
 * Auto-add focused batch only on first type pick in create mode if it's compatible with collection type.
 * Removes batch selections that are incompatible with new collection type.
 */
watch(
  () => info.type,
  (newType, oldType) => {
    // Skip during initialization to avoid clearing initial batch selections
    if (initializing.value) return
    if (newType === oldType) return
    if (!['create', 'update_batches'].includes(action.value)) return

    // Reset dataset if incompatible with new collection type
    if (
      selected.dataset &&
      !getAllowedDatasetTypes(newType).includes(selected.dataset.dataset_type)
    ) {
      selected.dataset = null
    }

    // Drop only batches whose type is no longer allowed
    const allowed = allowedBatchTypes.value
    batches.selected = batches.selected.filter((b) => allowed.includes(b.sample_batch_type))

    // Auto-add focused batch only on first type pick (null -> type) in create mode
    if (action.value === 'create' && !oldType && newType) {
      const batch = app.data.batch.focused
      if (batch && allowedBatchTypes.value.includes(batch.sample_batch_type)) {
        batches.selected = [clone(batch)]
        // sync selected.dataset so it's visible in the batches tab listbox
        if (selected.dataset?.dataset_id !== batch.dataset_id) {
          selected.dataset =
            app.data.dataset.list.find((d) => d.dataset_id === batch.dataset_id) ?? null
        }
      }
    }
  }
)

/**
 * Load batches when dataset or collection type changes.
 * Updates checkbox state to reflect current dataset selection.
 */
watch([() => selected.dataset, () => info.type], async ([newDataset, newType]) => {
  // Skip during initialization - init handles the first load
  if (initializing.value) return

  // Skip if no dataset or type (invalid state)
  if (!newDataset || !newType) {
    batches.loaded = []
    return
  }

  // Load batches for the new dataset
  await loadBatches(newDataset, newType)
})

// Auto-clear search when switching to manual input mode
watchEffect(() => {
  if (selected.source == 'input') {
    selected.search = ''
  }
})

/**
 * Load compounds whenever the source collection changes or the dialog opens.
 * Watching `visible` ensures compounds.loaded is repopulated after init()
 * clears it on each open, even when selected.collection resets to the same
 * value ('all-compounds') and wouldn't otherwise trigger a re-fetch.
 */
watch(
  [() => selected.collection, visible],
  async () => {
    if (!visible.value) return
    compounds.loaded =
      selected.collection === 'all-compounds'
        ? app.data.target.compound.list
        : ((await app.data.target.collection.read(selected.collection))?.target_compounds ?? [])
  },
  { immediate: true }
)
</script>

<template>
  <ConfirmDialog group="target-collection-create">
    <template #message="slotProps">
      <div style="display: flex; flex-direction: column; gap: 0.5rem">
        <p style="margin: 0">{{ slotProps.message.message.intro }}</p>
        <ul style="margin: 0; padding-left: 1.25rem">
          <li v-for="(item, i) in slotProps.message.message.items" :key="i">
            {{ item }}
          </li>
        </ul>
      </div>
    </template>
  </ConfirmDialog>
  <Dialog
    v-model:visible="visible"
    :header="title"
    :style="{ maxWidth: '95vw' }"
    :contentStyle="{ overflow: 'auto' }"
  >
    <!-- create or update -->
    <template v-if="['create', 'update', 'update_batches'].includes(action)">
      <div class="row">
        <FloatLabel>
          <InputText
            v-model="info.name"
            id="target-collection-name"
            :disabled="action == 'update_batches'"
            required
          />
          <label for="target-collection-name">Name</label>
        </FloatLabel>
        <FloatLabel style="flex-grow: 1">
          <InputText
            v-model="info.desc"
            id="target-collection-desc"
            :disabled="action == 'update_batches'"
            style="width: 100%"
          />
          <label for="target-collection-desc">Description</label>
        </FloatLabel>
        <SelectButton
          v-model="info.type"
          :options="collectionTypes"
          :allowEmpty="false"
          :disabled="action == 'update_batches'"
          :pt="
            app.ui.help.bottom(
              `
                <h1>Collection type</h1>
                <p>
                  <b>TARGETS</b> are compounds of interest to match in samples.
                  <b>DIAGNOSTICS</b> are used to monitor instrument performance.
                  <b>CALIBRANTS</b> are used for mass calibration.
                </p>
                <p>The type determines which batches the collection can be assigned to.</p>
              `,
              { layer, doc: app.ui.help.docUrl('concepts/#targeted-analysis') }
            )
          "
        />
      </div>
      <div class="row" style="margin-top: 0.5rem; align-items: center">
        <Select
          v-model="info.workspace_id"
          :options="workspaceScopeOptions"
          optionLabel="label"
          optionValue="value"
          placeholder="Select workspace scope"
          :disabled="scopeDisabled"
          v-tooltip.top="{
            value:
              'Choose which workspace this collection belongs to, or make it global for all workspaces',
            showDelay: 500
          }"
          style="min-width: 14rem"
        >
          <template #value="{ value }">
            <div class="row" style="align-items: center; gap: 0.5rem">
              <span
                :class="value === GLOBAL_SENTINEL ? 'pi ph ph-globe' : 'pi ph ph-briefcase'"
                style="font-size: 1rem; opacity: 0.7"
              />
              <span>{{
                // Non-admins have no Global option in the list but can still
                // view global collections (e.g. managing their batches)
                value === GLOBAL_SENTINEL
                  ? 'Global (all workspaces)'
                  : (workspaceScopeOptions.find((o) => o.value === value)?.label ??
                    'Select workspace scope')
              }}</span>
            </div>
          </template>
        </Select>
      </div>
      <Tabs v-model:value="selected.tab" lazy>
        <TabList>
          <Tab value="compounds" :disabled="action == 'update_batches'">Compounds</Tab>
          <Tab
            value="batches"
            :disabled="action == 'update' || (action === 'create' && !info.type)"
          >
            <span
              v-tooltip.top="
                action === 'create' && !info.type ? 'Please select a collection type first' : null
              "
              style="pointer-events: auto; display: inline-block"
              tabindex="0"
            >
              Batches
            </span>
          </Tab>
        </TabList>
        <TabPanels>
          <!-- compounds -->
          <TabPanel value="compounds">
            <div class="selector">
              <div
                class="row"
                style="align-items: stretch; height: 450px; gap: 0.5rem; min-width: 1072px"
              >
                <Panel
                  :pt="
                    app.ui.help.right(
                      `
                        <h1>Paste compounds</h1>
                        <p>Copy cells from a spreadsheet and paste them anywhere in this panel:</p>
                        <ul>
                          <li><b>1 column:</b> formula</li>
                          <li><b>2 columns:</b> name, formula</li>
                          <li><b>3 columns:</b> name, formula, CAS number</li>
                        </ul>
                        <p>
                          Only the formula is required. Rows matching an existing compound by
                          formula or CAS number reuse it instead of creating a duplicate, and
                          each row shows whether it will be created, added, kept or removed
                          when you save.
                        </p>
                      `,
                      { layer, doc: app.ui.help.docUrl('guides/target-collections/#add-compounds') }
                    )
                  "
                >
                  <div
                    class="row"
                    style="margin-bottom: 1rem; align-items: flex-start; min-width: 500px"
                  >
                    <h4 style="margin: 0" v-if="action == 'update'">Collection changes</h4>
                    <h4 style="margin: 0" v-else-if="action == 'create'">Collection</h4>
                    <span
                      :style="`
                        opacity: 0.5;
                        font-style: italic;
                        max-width: 250px;
                        font-size: smaller;
                        text-align: right;
                      `"
                    >
                      Add compounds by pasting spreadsheet cells here (a formula column alone, or
                      name + formula + optional CAS) or by using the panel to the right.
                    </span>
                  </div>
                  <BaseClipboardContext
                    hideInitMessage
                    @validated="({ data }) => loadSpreadsheet({ rows: data })"
                    :parse="parseCompoundPaste"
                    :validate="validateCompoundPaste"
                    :persistMessage="changes.length == 0"
                  >
                    <DataTable
                      :dataKey="
                        (item) =>
                          item.target_compound_id ||
                          `create_${item.target_compound_formula}_${item.target_compound_name || ''}_${item.cas_number || ''}`
                      "
                      v-if="changes.length"
                      :value="changes"
                      sortMode="multiple"
                      :multiSortMeta="[
                        { field: 'status', order: 1 },
                        { field: 'target_compound_formula', order: 1 }
                      ]"
                      scrollable
                      scrollHeight="360px"
                      :virtualScrollerOptions="{ itemSize: 49.69 }"
                      style="height: 360px; min-width: 500px"
                    >
                      <Column
                        header="Status"
                        field="status"
                        key="status"
                        columnKey="status"
                        sortable
                      >
                        <template #body="slotProps">
                          <div style="display: flex; align-items: center; height: 100%">
                            <Message
                              v-if="slotProps.data.status == '1 create'"
                              severity="success"
                              icon="pi pi-check"
                              :closable="false"
                              :pt="statusBadgePt"
                            >
                              Create
                            </Message>
                            <Message
                              v-else-if="slotProps.data.status == '2 add'"
                              severity="info"
                              icon="pi pi-plus"
                              :closable="false"
                              :pt="statusBadgePt"
                            >
                              Add
                            </Message>
                            <Message
                              v-else-if="slotProps.data.status == '3 keep'"
                              severity="secondary"
                              icon="pi pi-thumbtack"
                              :closable="false"
                              :pt="statusBadgePt"
                            >
                              Keep
                            </Message>
                            <Message
                              v-else-if="slotProps.data.status == '4 remove'"
                              severity="warn"
                              icon="pi pi-exclamation-triangle"
                              :closable="false"
                              :pt="removeBadgePt"
                            >
                              Remove
                            </Message>
                          </div>
                        </template>
                      </Column>
                      <Column
                        v-for="{ field, label } of columns.filter(
                          ({ field }) => field !== 'status'
                        )"
                        :key="field"
                        :field="field"
                        :header="label"
                        sortable
                      >
                        <template #body="{ data }">
                          <div
                            v-tooltip="data[field]"
                            style="
                              max-width: 100px;
                              white-space: nowrap;
                              overflow: hidden;
                              text-overflow: ellipsis;
                            "
                          >
                            {{ data[field] }}
                          </div>
                        </template>
                      </Column>
                      <Column headerStyle="width: 3rem">
                        <template #body="slotProps">
                          <Button
                            v-if="slotProps.data.status == '4 remove'"
                            @click="restore(slotProps.data)"
                            icon="pi pi-plus"
                            severity="secondary"
                            text
                          />
                          <Button
                            v-else
                            @click="remove(slotProps.data)"
                            icon="pi pi-times"
                            severity="secondary"
                            text
                          />
                        </template>
                      </Column>
                    </DataTable>
                    <div
                      v-else
                      style="
                        width: 100%;
                        min-width: 500px;
                        min-height: 360px;
                        display: flex;
                        align-items: center;
                        justify-content: center;
                      "
                    >
                      <i style="margin-bottom: 5rem">No compounds added yet</i>
                    </div>
                  </BaseClipboardContext>
                </Panel>
                <Panel
                  style="flex: 1; min-width: 500px"
                  :pt="
                    app.ui.help.left(
                      `
                        <h1>Add compounds</h1>
                        <p>
                          <b>Import existing</b> selects compounds from another collection or
                          from all known compounds.
                        </p>
                        <p>
                          <b>Create new</b> adds a single compound manually. The formula is
                          required; name and CAS number are optional.
                        </p>
                      `,
                      { layer, doc: app.ui.help.docUrl('guides/target-collections/#add-compounds') }
                    )
                  "
                >
                  <div class="row" style="align-items: flex-start">
                    <h4 style="margin-bottom: 0.5rem">Add compounds</h4>
                    <SelectButton
                      v-model="selected.source"
                      :allowEmpty="false"
                      :options="[
                        {
                          label: 'Import existing',
                          value: 'collection'
                        },
                        { label: 'Create new', value: 'input' }
                      ]"
                      optionLabel="label"
                      optionValue="value"
                    />
                  </div>
                  <div
                    class="row"
                    style="align-items: flex-end"
                    v-if="selected.source == 'collection'"
                  >
                    <FloatLabel>
                      <label>Collection</label>
                      <Select
                        v-model:modelValue="selected.collection"
                        :options="[
                          {
                            target_collection_id: 'all-compounds',
                            target_collection_name: 'All compounds'
                          },
                          ...app.data.target.collection.list.filter((coll) =>
                            action !== 'create'
                              ? coll.target_collection_id !== original.target_collection_id
                              : true
                          )
                        ]"
                        optionLabel="target_collection_name"
                        optionValue="target_collection_id"
                        dataKey="target_collection_id"
                        id="target-collection-source"
                        style="min-width: 200px"
                        filter
                        resetFilterOnHide
                      >
                        <template #option="{ option }">
                          <div style="display: flex; align-items: center; gap: 0.4rem">
                            <span
                              v-if="option.target_collection_id !== 'all-compounds'"
                              :class="
                                !option.workspace_id ? 'pi ph ph-globe' : 'pi ph ph-briefcase'
                              "
                              style="font-size: 0.85rem; opacity: 0.6"
                            />
                            <span>{{ option.target_collection_name }}</span>
                          </div>
                        </template>
                      </Select>
                    </FloatLabel>
                    <FloatLabel style="flex-grow: 1">
                      <IconField class="full">
                        <InputIcon>
                          <i class="pi pi-search" />
                        </InputIcon>
                        <InputText
                          v-model="selected.search"
                          placeholder="Search compounds"
                          style="width: 100%"
                        />
                      </IconField>
                    </FloatLabel>
                  </div>
                  <!--
                    The branches in this panel share one fragment and Vue keys
                    the ones it compiles itself by number (0 for the row above,
                    2 for the manual-input div below). Keep this remount key
                    non-numeric so it cannot collide with them - a bare
                    `key.targets` starts at 0 and makes every source toggle warn
                    about duplicate keys.
                  -->
                  <DataTable
                    v-if="selected.source == 'collection'"
                    :key="`targets-${key.targets}`"
                    dataKey="target_compound_id"
                    v-model:selection="compounds.selected"
                    :value="
                      compounds.loaded.filter((comp) => {
                        const query = selected.search.toLowerCase()
                        const nameMatch = comp.target_compound_name?.toLowerCase().includes(query)
                        const formulaMatch = comp.target_compound_formula
                          ?.toLowerCase()
                          .includes(query)
                        const casMatch = comp.cas_number?.toLowerCase().includes(query)
                        return nameMatch || formulaMatch || casMatch
                      })
                    "
                    sortField="mz"
                    scrollable
                    scrollHeight="310px"
                    :virtualScrollerOptions="{ itemSize: 37.4 }"
                    style="height: 310px; min-width: 500px"
                  >
                    <Column selectionMode="multiple" header="" headerStyle="width: 3rem">
                      <template #header>
                        <Checkbox
                          :binary="true"
                          v-model="allCompoundsSelected"
                          :disabled="compounds.loaded.length == 0"
                          inputClass="custom"
                        />
                      </template>
                    </Column>
                    <Column
                      v-for="{ field, label } of columns.filter(({ field }) => field !== 'status')"
                      :key="field"
                      :field="field"
                      :header="label"
                      sortable
                    >
                      <template #body="{ data }">
                        <div
                          v-tooltip="data[field]"
                          style="
                            max-width: 100px;
                            white-space: nowrap;
                            overflow: hidden;
                            text-overflow: ellipsis;
                          "
                        >
                          {{ data[field] }}
                        </div>
                      </template>
                    </Column>
                  </DataTable>
                  <div
                    v-if="selected.source == 'input'"
                    style="min-width: 500px; height: 100%; display: grid; place-items: center"
                  >
                    <div class="col" style="gap: 0.5rem; margin-top: 2rem">
                      <FloatLabel>
                        <InputText v-model="add.formula" id="add-compound-formula" />
                        <label for="add-compound-formula">Formula</label>
                      </FloatLabel>
                      <FloatLabel>
                        <InputText v-model="add.name" id="add-compound-name" />
                        <label for="add-compound-name">Name</label>
                      </FloatLabel>
                      <FloatLabel>
                        <InputText v-model="add.cas" id="add-compound-cas" />
                        <label for="add-compound-cas">CAS Number</label>
                      </FloatLabel>
                      <Button
                        :label="addCompoundButtonConfig?.label ?? 'Add compound'"
                        icon="pi pi-plus"
                        @click="addCompound"
                        :disabled="addCompoundButtonDisabled"
                        :severity="addCompoundButtonConfig?.severity ?? 'primary'"
                        v-tooltip="addCompoundButtonConfig?.tooltip ?? 'Add compound'"
                        style="width: 210px; margin-top: 2rem"
                      />
                    </div>
                  </div>
                </Panel>
              </div>
            </div>
          </TabPanel>
          <!-- batches -->
          <TabPanel value="batches">
            <div
              class="row"
              style="height: 450px; align-items: stretch; gap: 0.5rem; min-width: 1072px"
            >
              <Panel style="flex: 1; min-width: 500px">
                <div class="row" style="margin-bottom: 1rem; align-items: flex-start">
                  <h4 style="margin: 0">Datasets</h4>
                  <span
                    :style="{
                      opacity: 0.5,
                      fontStyle: 'italic',
                      maxWidth: '250px',
                      fontSize: 'smaller',
                      textAlign: 'right'
                    }"
                  >
                    Pick a dataset to browse and assign its batches.
                  </span>
                </div>
                <Listbox
                  v-model="selected.dataset"
                  dataKey="dataset_id"
                  optionLabel="label"
                  :options="filteredDatasets"
                  scrollHeight="350px"
                  :virtualScrollerOptions="{ itemSize: 28.41 }"
                  style="width: 100%; min-height: 350px"
                  :pt="{ root: { style: 'border: 0' } }"
                />
              </Panel>
              <!-- batches -->
              <Panel style="flex: 1; min-width: 500px">
                <div
                  class="row"
                  style="margin-bottom: 1rem; align-items: flex-start; min-width: 500px"
                >
                  <h4 style="margin: 0">Batches</h4>
                  <span
                    :style="{
                      opacity: 0.5,
                      fontStyle: 'italic',
                      maxWidth: '250px',
                      fontSize: 'smaller',
                      textAlign: 'right'
                    }"
                  >
                    Toggle to which batches this collection will be assigned.
                  </span>
                </div>
                <DataTable
                  dataKey="sample_batch_id"
                  v-model:selection="datasetBatchSelection"
                  :value="batches.loaded"
                  scrollable
                  scrollHeight="350px"
                  :virtualScrollerOptions="{ itemSize: 36.34 }"
                  style="width: 100%; min-width: 500px"
                  sortField="sample_batch_utc_created"
                  :sortOrder="-1"
                >
                  <Column selectionMode="multiple" header="" headerStyle="width: 3rem">
                    <template #header>
                      <Checkbox
                        :binary="true"
                        v-model="allBatchesSelected"
                        :disabled="batches.loaded.length == 0"
                        inputClass="custom"
                      />
                    </template>
                  </Column>
                  <Column header="Batch" field="sample_batch_name">
                    <template #body="{ data }">
                      <div
                        v-tooltip="data.sample_batch_name"
                        style="
                          max-width: 350px;
                          white-space: nowrap;
                          overflow: hidden;
                          text-overflow: ellipsis;
                        "
                      >
                        {{ data.sample_batch_name }}
                      </div>
                    </template>
                  </Column>
                </DataTable>
              </Panel>
            </div>
          </TabPanel>
        </TabPanels>
      </Tabs>
    </template>
    <!-- delete -->
    <template v-else-if="action == 'delete'">
      <Message
        severity="warn"
        icon="pi pi-exclamation-triangle"
        :closable="false"
        style="margin-bottom: 1rem"
      >
        <b>This will permanently delete the collection itself.</b> <br />
        To remove a collection from a batch without deleting it, right-click the batch and select
        "Edit batch targets" instead.
      </Message>
      <p style="max-width: 50ch">
        Would you like to keep or remove compounds from {{ info.name }}
        that are not part of any other collection?
      </p>
      <div class="col" style="align-items: flex-start; margin: 2rem; gap: 1rem">
        <div class="row">
          <RadioButton
            v-model="deleteOrphans"
            :value="true"
            name="orphans"
            inputId="keep-orphans"
          />
          <label for="keep-orphans"> Delete the collection and its unique compounds </label>
        </div>
        <div class="row">
          <RadioButton
            v-model="deleteOrphans"
            :value="false"
            name="orphans"
            inputId="delete-orphans"
          />
          <label for="delete-orphans"> Delete the collection but keep the unique compounds </label>
        </div>
      </div>
    </template>
    <!-- dialog menu -->
    <menu>
      <Button label="Cancel" severity="secondary" @click="action = null" />
      <Button
        :label="executeLabel"
        @click="execute"
        :disabled="invalidated"
        v-tooltip.top="
          invalidated && action === 'create' && !info.type
            ? 'Please select a collection type'
            : null
        "
      />
    </menu>
  </Dialog>
</template>

<style scoped>
.row {
  align-items: flex-end;
}
.row > * {
  margin-bottom: 0.5rem;
}

.col {
  display: flex;
  gap: 0.5rem;
}

:deep(.p-panel-header) {
  padding-top: 0;
}
:deep(.p-datatable-column-header-content :not(.custom) + .p-checkbox-box) {
  display: none;
}
</style>
