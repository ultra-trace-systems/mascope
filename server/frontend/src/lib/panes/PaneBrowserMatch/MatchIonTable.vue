<script setup>
import { ref, computed, watch, onBeforeUnmount } from 'vue'

import Button from 'primevue/button'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import InputText from 'primevue/inputtext'
import Select from 'primevue/select'
import { FilterMatchMode, FilterOperator } from '@primevue/core/api'

import { BaseTabbedPanel, BaseMatchTag, BaseCopyableField } from '@/lib/base'
import { PopoverTargetCompoundAdd } from '@/lib/dialogs'
import { num } from '@/lib/formatters'
import { collectionTypeIcons } from '@/lib/constants'
import { prettyTrim } from '@/lib/utils'

import { useApp } from '@/stores'
import {
  useCollectionContextMenu,
  useIonContextMenu,
  useIonScroller,
  useIonTableCustomizer
} from './stores'
import { useSampleScroller } from '@/lib/panes/PaneBrowserSample/stores'
import MatchCollectionContextMenu from './MatchCollectionContextMenu.vue'
import MatchIonContextMenu from './MatchIonContextMenu.vue'
import MatchIonTableCustomizer from './MatchIonTableCustomizer.vue'

const app = useApp()
const ionTable = ref(null)
const collectionContextMenu = useCollectionContextMenu()
const ionContextMenu = useIonContextMenu()
const sampleScroller = useSampleScroller()
const ionScroller = useIonScroller()
const ionTableCustomizer = useIonTableCustomizer()

const isColumnVisible = (field) => {
  const cols = ionTableCustomizer.config.columns
  if (!cols || cols.length === 0) return true
  return cols.some((c) => c.field === field)
}

// --- Breadcrumb Navigation ---
const breadcrumb = computed(() => {
  const collection = app.data.match.collection.focused
  if (!collection) return null

  const entityName = app.data.sample.focused
    ? app.data.sample.focused.sample_item_name
    : app.data.batch.focused?.sample_batch_name || ''

  return {
    items: [
      {
        icon: 'pi pi-hashtag',
        disabled: false,
        tooltip: 'Back to batch',
        action: () => app.data.sample.unfocus()
      },
      {
        icon: app.data.sample.focused ? 'pi pi-tag' : 'pi pi-hashtag',
        label: `${prettyTrim(entityName, 25)}`,
        disabled: true,
        tooltip: app.data.sample.focused
          ? `Matched ions for sample:\n ${app.data.sample.focused.sample_item_name}`
          : `Matched ions for batch:\n ${app.data.batch.focused.sample_batch_name}`
      },
      {
        icon: 'pi ph ph-crosshair',
        label: 'Target collections',
        action: () => app.data.match.collection.unfocus(),
        tooltip: 'Back to target collections',
        contextMenu: {
          items: collectionContextMenu.entries.value
        },
        contextMenuHandler: async (event) => {
          // Trigger "edit batch targets" context menu from breadcrumb
          await collectionContextMenu.onClick(event)
        }
      },
      {
        icon: collectionTypeIcons[collection.target_collection_type] || 'pi ph ph-target',
        label: `${collection.target_collection_name}`,
        action: () => {}, // Dummy action to switch cursor to pointer
        tooltip: collection.target_collection_description
          ? `${collection.target_collection_description} (${collection.target_collection_type?.toLowerCase()})`
          : collection.target_collection_type?.toLowerCase() || 'Collection targets',
        contextMenu: {
          items: collectionContextMenu.entries.value
        },
        contextMenuHandler: (event) => {
          // Manually trigger collection context menu from breadcrumb
          collectionContextMenu.selection = collection
          collectionContextMenu.ref?.toggle(event)
        }
      },
      {
        icon: 'pi ph ph-atom',
        label: `${app.data.match.ion.list.length} ions`,
        disabled: true
      }
    ].slice(app.data.sample.focused ? 0 : 1)
  }
})

// --- Data & Computed ---
const mechanismMap = computed(
  () =>
    new Map(
      app.data.ionization.mechanism.list.map((m) => [
        m.ionization_mechanism,
        m.ionization_mechanism_id
      ])
    )
)

// --- State Management ---
// expandable rows state - only one ion can be expanded at a time
const expandedRows = ref({})
const expandedIonId = ref(null)
const expanderIcon = computed(() =>
  app.data.sample.focused ? 'pi ph ph-seal-question' : 'pi pi-tag'
)

// --- Filtering ---
// Filters configuration for each column
const filters = ref({
  target_ion_formula: {
    operator: FilterOperator.AND,
    constraints: [{ value: null, matchMode: FilterMatchMode.CONTAINS }]
  },
  target_compound_name: {
    operator: FilterOperator.AND,
    constraints: [{ value: null, matchMode: FilterMatchMode.CONTAINS }]
  },
  target_compound_formula: {
    operator: FilterOperator.AND,
    constraints: [{ value: null, matchMode: FilterMatchMode.CONTAINS }]
  },
  ionization_mechanism: {
    operator: FilterOperator.AND,
    constraints: [{ value: null, matchMode: FilterMatchMode.EQUALS }]
  }
})

// Unique values for dropdown filters
const filterOptions = computed(() => ({
  mechanisms: [
    ...new Set(app.data.match.ion.list.map((ion) => ion.ionization_mechanism).filter(Boolean))
  ]
}))

// --- Row Expansion for match ion visualization and isotopes ---
const focusMatchIon = async (ionId) => {
  if (!app.data.sample.focusedId) return
  await app.data.match.visualized.set({
    sampleId: app.data.sample.focusedId,
    ionId: ionId,
    collectionId: app.data.match.collection.focusedId
  })
}

const unfocusMatchIon = async () => {
  await app.data.match.visualized.clear()
}

function toggleRowExpansion(ionId) {
  if (expandedRows.value[ionId]) {
    // Collapse
    expandedRows.value = {}
    expandedIonId.value = null
    unfocusMatchIon()
  } else {
    // Expand (and collapse others)
    expandedRows.value = { [ionId]: true }
    expandedIonId.value = ionId
    focusMatchIon(ionId)
    app.ui.tab.active = 'match'
  }
}

const focusSampleWithBestMatch = (sampleId) => {
  if (!sampleId) return
  app.data.sample.focus({ sample_item_id: sampleId })
  sampleScroller.scrollToSample(sampleId)
}

const autoSelectTopMatches = (top = 30) => {
  const ions = app.data.match.ion.list
  if (ions.length > 0) {
    // Sort ions by match_score descending, filter out match_score <= 0, and take up to 'top' results
    const topIons = [...ions]
      .filter((ion) => (ion.match?.match_score || 0) > 0)
      .sort((a, b) => (b.match?.match_score || 0) - (a.match?.match_score || 0))
      .slice(0, top)
    app.data.match.ion.selected = topIons
  } else {
    app.data.match.ion.selected = []
  }
}

// Handle Ctrl+A to select all rows (virtual scroller only selects visible rows by default)
const onKeyDown = (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === 'a') {
    event.preventDefault()
    // Read processedData to get filtered rows
    const filteredRows = ionTable.value?.processedData ?? app.data.match.ion.list
    app.data.match.ion.selected = [...filteredRows]
  }
}

// --- Watchers ---

// Watch for table ref to become available and bind to scroller
watch(
  ionTable,
  (newTableRef) => {
    if (newTableRef) {
      ionScroller.bind(
        newTableRef,
        () => app.data.match.ion.list,
        () => ({ sortField: 'match.match_score', sortOrder: -1 })
      )
    }
  },
  { immediate: true }
)

// Watch for focused ion and scroll to it
watch(
  () => app.data.match.visualized?.ion?.target_ion_id,
  (ionId) => {
    if (ionId) {
      ionScroller.scrollToIon(ionId)
    }
  }
)

onBeforeUnmount(() => {
  ionScroller.bind(
    null,
    () => [],
    () => ({ sortField: 'match.match_score', sortOrder: -1 })
  )
})

// Watch for ion list changes to auto-select top matches when collection is opened
watch(
  () => app.data.match.ion.list,
  () => {
    // Auto-select top matches if no ions are currently selected
    if (app.data.match.ion.selected.length) return
    autoSelectTopMatches()
  }
)

// Watch for collection changes to reset expansion state
watch(
  () => app.data.match.collection.focused,
  () => {
    expandedRows.value = {}
    expandedIonId.value = null
    ionContextMenu.clear()
  }
)

// Watch for match ion visualization changes to sync expansion state
// This is to implement 2-way binding with batch chart click events
watch(
  () => app.data.match.visualized.ion,
  (ion) => {
    if (ion) {
      // Prevent reloading same isotopes
      if (expandedIonId.value === ion.target_ion_id) return
      // Expand the corresponding row
      expandedRows.value = { [ion.target_ion_id]: true }
      expandedIonId.value = ion.target_ion_id
    }
  }
)

// Watch ion selection to collapse expanded row when changed
// This is to avoid stale expansion when changing selection
watch(
  () => app.data.match.ion.selected,
  (newSelection, oldSelection) => {
    if (newSelection.length === oldSelection.length) {
      // Selection not actually changed, sample and thus match.ion data changed.
      // Skip clearing
      return
    }
    expandedRows.value = {}
    expandedIonId.value = null
    unfocusMatchIon()
  }
)

// Watch mechanism focus and sync to dropdown filter
watch(
  () => app.data.ionization.mechanism.focused,
  (focused) => {
    if (!focused) {
      // Clear dropdown when mechanism unfocused externally (chip, etc)
      filters.value.ionization_mechanism.constraints[0].value = null
    }
  }
)

// Watch mechanism filter value and sync unfocus when cleared externally (filter menu "Clear" button)
watch(
  () => filters.value.ionization_mechanism.constraints[0].value,
  (value) => {
    if (!value) {
      app.data.ionization.mechanism.unfocus()
    }
  }
)
</script>

<template>
  <BaseTabbedPanel
    :breadcrumb="breadcrumb"
    :loading="app.data.match.ion.pending"
    :error="app.data.match.ion.error"
    :onRetry="() => app.data.match.ion.load('retry')"
    :pt="
      app.ui.help.right(
        `<h1>Match Browser: Ions</h1>
        <p>
        Shows matched ions of the selected collection. Use column filters to search and filter results.
        </p>
        <p>
        If a single sample is selected, shows match scores for each ion based on the sample data.
        Click on
        the match icon <span class='pi ph ph-seal-question'></span> on a row to visualize the match in Match View,
        and see the individual isotope matches.
        </p>
        <p>
        If multiple samples are selected, shows the top match score of all batch samples for each ion.
        Click on the sample icon <span class='pi pi-tag'></span> on a row to select the sample with the best match.
        </p>
        <p>
        Right click on any ion to manage its parent compound.
        </p>`,
        { doc: app.ui.help.docUrl('guides/target-collections/#read-the-results') }
      )
    "
  >
    <template #menu>
      <div class="row">
        <PopoverTargetCompoundAdd :collection="app.data.match.collection.focused" />
        <MatchIonTableCustomizer />
      </div>
    </template>

    <DataTable
      ref="ionTable"
      :value="app.data.match.ion.list"
      dataKey="target_ion_id"
      v-model:selection="app.data.match.ion.selected"
      v-model:expandedRows="expandedRows"
      v-model:filters="filters"
      selectionMode="multiple"
      :metaKeySelection="true"
      contextMenu
      v-model:contextMenuSelection="ionContextMenu.selection"
      @rowContextmenu="
        async (event) => {
          event.originalEvent.stopPropagation()
          event.originalEvent.preventDefault()
          await ionContextMenu.onClick(event)
        }
      "
      @keydown="onKeyDown"
      :expandedRowIcon="expanderIcon"
      :collapsedRowIcon="expanderIcon"
      filterDisplay="menu"
      resizableColumns
      size="small"
      scrollable
      scrollHeight="flex"
      :virtualScrollerOptions="{ itemSize: 35.74 }"
      sortField="match.match_score"
      :sortOrder="-1"
      :pt="{
        bodyRow: ({ context }) => ({ id: app.data.match.ion.list[context.index]?.target_ion_id })
      }"
    >
      <template #empty>No match ions found.</template>

      <!-- Expander Column. Two actions share it: with a sample focused it
           visualizes that ion's match in the Match tab, without one it selects
           the batch sample that matches best. Both render whatever the
           peak_assignment flag says - the targeted workflow keeps its per-ion
           view when assignment is on, since the two coexist. -->
      <Column expander style="width: 3rem">
        <template #body="{ data }">
          <Button
            :icon="app.data.sample.focused ? 'pi ph ph-seal-question' : 'pi pi-tag'"
            size="small"
            text
            :severity="expandedIonId === data.target_ion_id ? 'info' : 'secondary'"
            v-tooltip.top="
              app.data.sample.focusedId
                ? 'Visualize ion match'
                : 'Click to select sample with the best match'
            "
            @click="
              async () => {
                if (app.data.sample.focusedId) {
                  toggleRowExpansion(data.target_ion_id)
                } else {
                  focusSampleWithBestMatch(data.match.sample_item_id)
                }
              }
            "
          />
        </template>
      </Column>

      <!-- Match Score Column -->
      <Column sortable sortField="match.match_score" class="match-column">
        <template #header> <span class="pi ph ph-seal-percent" /> </template>
        <template #body="{ data }">
          <BaseMatchTag
            :match-score="data.match?.match_score"
            :match-category="data.match?.match_category"
            :alarming="data.match?.alarming"
            :tooltip="
              data.match?.sample_peak_intensity_sum
                ? `Peak intensity: ${num.peakIntensity.format(data.match.sample_peak_intensity_sum)} (cps)`
                : 'No peak intensity data'
            "
          />
        </template>
      </Column>

      <!-- Ion Formula Column -->
      <Column
        v-if="isColumnVisible('target_ion_formula')"
        field="target_ion_formula"
        header="Ion"
        sortable
        style="min-width: 10rem"
      >
        <template #body="{ data }">
          <div
            :id="`target-ion-${data.target_ion_id}`"
            class="row"
            style="justify-content: flex-start"
          >
            <BaseCopyableField :field="data.target_ion_formula" />
          </div>
        </template>
        <template #filter="{ filterModel, filterCallback }">
          <InputText
            v-model="filterModel.value"
            type="text"
            @input="filterCallback()"
            placeholder="Search ion..."
            size="small"
          />
        </template>
      </Column>

      <!-- Compound name column -->
      <Column
        v-if="isColumnVisible('target_compound_name')"
        field="target_compound_name"
        header="Name"
        sortable
        style="min-width: 10rem"
      >
        <template #body="{ data }">
          <BaseCopyableField :field="data.target_compound_name" />
        </template>
        <template #filter="{ filterModel, filterCallback }">
          <InputText
            v-model="filterModel.value"
            type="text"
            @input="filterCallback()"
            placeholder="Search compound..."
            size="small"
          />
        </template>
      </Column>

      <!-- Compound formula column -->
      <Column
        v-if="isColumnVisible('target_compound_formula')"
        field="target_compound_formula"
        header="Compound"
        sortable
        style="min-width: 10rem"
      >
        <template #body="{ data }">
          <BaseCopyableField
            :field="data.target_compound_formula"
            :tooltip="data.target_compound_name"
          />
        </template>
        <template #filter="{ filterModel, filterCallback }">
          <InputText
            v-model="filterModel.value"
            type="text"
            @input="filterCallback()"
            placeholder="Search compound..."
            size="small"
          />
        </template>
      </Column>

      <!-- Ionization Mechanism Column -->
      <Column
        v-if="isColumnVisible('ionization_mechanism')"
        field="ionization_mechanism"
        header="Mechanism"
        sortable
        style="min-width: 10rem"
        :filterMatchModeOptions="[{ label: 'Equals', value: 'equals' }]"
      >
        <template #body="{ data }">
          <BaseCopyableField :field="data.ionization_mechanism"> </BaseCopyableField>
        </template>
        <template #filter="{ filterModel, filterCallback }">
          <Select
            v-model="filterModel.value"
            @change="
              (e) => {
                filterCallback()
                e.value
                  ? app.data.ionization.mechanism.focus({
                      ionization_mechanism_id: mechanismMap.get(e.value)
                    })
                  : app.data.ionization.mechanism.unfocus()
              }
            "
            :options="filterOptions.mechanisms"
            placeholder="Any mechanism"
            size="small"
            :showClear="true"
          />
        </template>
      </Column>
    </DataTable>
  </BaseTabbedPanel>

  <MatchCollectionContextMenu />
  <MatchIonContextMenu />
</template>

<style scoped>
/* Disable hover effects on expansion content */
:deep(.p-datatable-row-expansion) {
  background-color: var(--p-datatable-row-background) !important;
}
:deep(.p-datatable-row-expansion:hover) {
  background-color: var(--p-datatable-row-background) !important;
}
:deep(.p-datatable-row-expansion .match-isotope-container) {
  background-color: transparent !important;
}
:deep(.p-button.p-button-secondary:hover) {
  background-color: var(--p-button-contrast-hover-color) !important;
}
:deep(.p-button.p-button-info:hover) {
  background-color: var(--p-button-contrast-hover-color) !important;
}
</style>
