<script setup>
import { ref } from 'vue'

import Button from 'primevue/button'
import Column from 'primevue/column'
import DataTable from 'primevue/datatable'
import Popover from 'primevue/popover'
import ProgressSpinner from 'primevue/progressspinner'
import Select from 'primevue/select'

import {
  BaseEmptyState,
  BaseLoadError,
  BaseProvisionalMark,
  BaseTabbedPanel,
  BaseTierTag
} from '@/lib/base'
import { num } from '@/lib/formatters'
import { LEVEL_FILTER_OPTIONS } from '@/lib/levels'
import { useApp } from '@/stores'

import LedgerListingCell from './LedgerListingCell.vue'
import LedgerTierStrip from './LedgerTierStrip.vue'
import LedgerVerdictCell from './LedgerVerdictCell.vue'
import { useLedgerListing } from './useLedgerListing.js'

/**
 * The assignment ledger: a formula at an m/z with a tier, per peak of a sample
 * or per batch peak of a batch. One component for both scopes; the panes
 * (PaneBrowserAssignment, PaneBrowserBatchPeaks) are its configurations - the
 * rows and how they are read, the scope's extra columns and strip items, the
 * selection - and it draws what the two share, the same way in both:
 *
 * - the header line, the scope's title and its count;
 * - the tier strip, multi-select, with the scope's items and the view menu at
 *   its end;
 * - one inline spinner, one inline load error and one empty state where the
 *   table would be, so the strip and the bar above stay usable;
 * - the shared columns, sentence case: m/z, intensity, the formula, what a list
 *   calls it, the tier, the engine's tier and the level where any row has one,
 *   and the verdict, sortable in both scopes. A cell with nothing in it is
 *   blank.
 *
 * Sorting and filtering are the scope's (useLedgerSort, useLedgerFilters):
 * the table is `lazy`, so `rows` arrive filtered, sorted and folded as shown.
 * Every attribute the pane sets that is not a prop here - the selection, the
 * data key, the column filters, the row class - goes to the DataTable.
 */
defineOptions({ inheritAttrs: false })

const props = defineProps({
  scope: { type: String, required: true },
  /** The panel's title, and the count of what the ledger lists. */
  label: { type: String, required: true },
  icon: { type: String, default: null },
  count: { type: String, default: '' },
  countTooltip: { type: String, default: null },
  /** Help mode's card for the panel. */
  panelPt: { type: Object, default: null },

  /** The run's or the ledger's rows are loading, or failed to. */
  pending: { type: Boolean, default: false },
  error: { type: [Error, Object, String], default: null },
  errorFallback: { type: String, default: null },
  onRetry: { type: Function, default: null },
  /** No row passes the filters. */
  emptyTitle: { type: String, default: 'No rows match these filters' },

  rows: { type: Array, required: true },
  rowHeight: { type: Number, required: true },
  sortField: { type: String, default: null },
  sortOrder: { type: Number, default: null },
  /**
   * The row field each shared column reads: `mz`, `intensity`, `formula`,
   * `listing`, `level`, and `levelSort` for the level's rank.
   */
  fields: { type: Object, required: true },
  /** Header tooltips by column key. */
  headers: { type: Object, default: () => ({}) },
  intensityLabel: { type: String, default: 'Intensity' },

  /** The tier strip, and whether it is drawn at all. */
  strip: { type: Boolean, default: true },
  chips: { type: Array, required: true },
  activeTiers: { type: Set, required: true },
  engineView: { type: Boolean, default: false },
  engineViewTooltip: { type: String, default: '' },
  /** The view menu's cog: its tooltip and help card. */
  viewMenuTooltip: { type: String, default: '' },
  viewMenuPt: { type: Object, default: null },

  hasEngineTiers: { type: Boolean, default: false },
  engineTierTooltip: { type: Function, default: () => '' },
  /** Whether an isotopologue line shows an engine tier of its own. */
  engineTierOnLines: { type: Boolean, default: true },
  hasLevels: { type: Boolean, default: false },

  /** The verdict cell's props for a row (LedgerVerdictCell), and its help card. */
  verdictCell: { type: Function, required: true },
  verdictHelp: { type: Object, required: true }
})
defineEmits(['update:sortField', 'update:sortOrder', 'toggle-tier', 'open-verdict'])

const app = useApp()
const listingColumn = useLedgerListing()

const VIEW_MENU_IDS = { sample: 'assignment-view-menu', batch: 'batch-peak-view-menu' }
const viewMenu = ref()
const viewMenuOpen = ref(false)
const toggleViewMenu = (event) => viewMenu.value?.toggle(event)

// The table, for a scope that scrolls a row into view.
const table = ref()
defineExpose({ table, viewMenu })

const isLineRow = (row) => Boolean(row.isChild || row.parentId)
const intensityOf = (row) => {
  const value = row[props.fields.intensity]
  return value != null ? num.peakIntensity.format(value) : ''
}
</script>

<template>
  <BaseTabbedPanel :label="label" :icon="icon" :pt="panelPt">
    <template #menu>
      <span
        v-if="count"
        class="ledger-count"
        data-testid="ledger-count"
        v-tooltip.top="countTooltip"
        >{{ count }}</span
      >
    </template>

    <!-- What the scope reports above everything: a refused launch. -->
    <slot name="notices" />

    <!-- A state of the scope with no ledger to show (no sample, no runs). -->
    <slot v-if="$slots.state" name="state" />

    <div v-else class="col ledger" style="gap: 0.6rem; align-items: stretch">
      <slot name="above-strip" />

      <LedgerTierStrip
        v-if="strip"
        :chips="chips"
        :active="activeTiers"
        :engineView="engineView"
        :engineTooltip="engineViewTooltip"
        @toggle="$emit('toggle-tier', $event)"
      >
        <!-- The scope's own items and the view menu, together at the strip's end. -->
        <span class="strip-end">
          <slot name="strip" />
          <!-- At the end of the filter row, because that is what it holds. A
               cog, matching the table-controls button the sample and ion
               browsers put in the same corner. -->
          <Button
            class="view-menu-button"
            icon="pi pi-cog"
            severity="secondary"
            text
            size="small"
            aria-label="Ledger view options"
            aria-haspopup="dialog"
            :aria-controls="viewMenuOpen ? VIEW_MENU_IDS[scope] : undefined"
            :aria-expanded="viewMenuOpen"
            v-tooltip.top="viewMenuTooltip"
            :pt="viewMenuPt"
            @click="toggleViewMenu"
          />
          <!-- Named, because Popover gives its panel role="dialog"
               aria-modal="true" and nothing else: an unnamed dialog is announced
               as just "dialog". Both attributes land on that root. -->
          <Popover
            ref="viewMenu"
            :id="VIEW_MENU_IDS[scope]"
            aria-label="Ledger view options"
            @show="viewMenuOpen = true"
            @hide="viewMenuOpen = false"
          >
            <div class="view-menu"><slot name="view-menu" /></div>
          </Popover>
        </span>
      </LedgerTierStrip>

      <slot name="above-table" />

      <div v-if="pending" class="center loading-region">
        <ProgressSpinner />
      </div>
      <!-- Inline rather than in place of the panel, so the strip, its view
           menu and the bar above stay usable while only the rows are
           unavailable. -->
      <div v-else-if="error" class="center loading-region">
        <BaseLoadError :error="error" :fallback="errorFallback" :onRetry="onRetry" />
      </div>
      <DataTable
        v-else
        ref="table"
        v-bind="$attrs"
        :value="rows"
        size="small"
        scrollable
        scrollHeight="flex"
        :virtualScrollerOptions="{ itemSize: rowHeight }"
        lazy
        removableSort
        :sortField="sortField"
        :sortOrder="sortOrder"
        filterDisplay="menu"
        @update:sortField="$emit('update:sortField', $event)"
        @update:sortOrder="$emit('update:sortOrder', $event)"
      >
        <template #empty>
          <slot name="empty"><BaseEmptyState :title="emptyTitle" /></slot>
        </template>

        <slot name="lead-column" />

        <!-- The header tooltips sit on a span in the header slot, never on the
             Column itself: Column renders no element of its own. -->
        <Column :field="fields.mz" sortable style="min-width: 6rem">
          <template #header>
            <span v-tooltip.top="headers.mz">m/z</span>
          </template>
          <template #body="{ data }">{{ num.mz.format(data[fields.mz]) }}</template>
        </Column>

        <Column :field="fields.intensity" sortable style="min-width: 6rem">
          <template #header>
            <span v-tooltip.top="headers.intensity">{{ intensityLabel }}</span>
          </template>
          <template #body="{ data }">
            <span class="intensity">{{ intensityOf(data) }}</span>
            <slot name="intensity" :data="data" />
          </template>
        </Column>

        <Column :field="fields.formula" sortable style="min-width: 7rem">
          <template #header>
            <span v-tooltip.top="headers.formula">Formula</span>
          </template>
          <template #body="{ data }"><slot name="formula" :data="data" /></template>
          <template v-if="$slots['formula-filter']" #filter="filter">
            <slot name="formula-filter" v-bind="filter" />
          </template>
        </Column>

        <!-- An isotopologue line is its family's formula again, so it shows
             no listing of its own. -->
        <Column
          :field="fields.listing"
          sortable
          :style="{ minWidth: listingColumn.full ? '8rem' : '5rem' }"
        >
          <template #header>
            <span v-tooltip.top="headers.listing">{{
              listingColumn.full ? 'Listed as' : 'Listed'
            }}</span>
          </template>
          <template #body="{ data }">
            <LedgerListingCell
              v-if="!isLineRow(data)"
              :listing="data.listing"
              :target="data.target"
            />
          </template>
        </Column>

        <slot name="middle-columns" />

        <Column field="tierRank" sortable style="min-width: 7rem">
          <template #header>
            <span
              v-tooltip.top="headers.tier"
              v-help.top="{
                title: 'Confidence Tiers',
                helpKey: 'assignment-tiers',
                doc: app.ui.help.docUrl('how-it-works/peak-assignment/#confidence-tiers')
              }"
              >Tier</span
            >
            <BaseProvisionalMark />
          </template>
          <template #body="{ data }"><slot name="tier" :data="data" /></template>
        </Column>

        <!-- The producing engine's own tier, beside this server's rather than
             instead of it: the tier column is what the table sorts, filters and
             rolls up on, and a row where the two differ is the one worth
             opening. -->
        <Column v-if="hasEngineTiers" field="engineTierRank" sortable style="min-width: 7rem">
          <template #header>
            <span v-tooltip.top="headers.engineTier">Engine tier</span>
          </template>
          <template #body="{ data }">
            <BaseTierTag
              v-if="data.engine_tier && (engineTierOnLines || !isLineRow(data))"
              :tier="data.engine_tier"
              :tooltip="engineTierTooltip(data)"
            />
          </template>
        </Column>

        <!-- The identification level, beside the tiers and never in their
             place: what a reader may state of the row, a measured mass (5) to a
             confirmed structure (1). `field` stays the level so the filter
             menu keeps its constraint; `sortField` is the scale's rank. One
             rule, and no button to add a second. -->
        <Column
          v-if="hasLevels"
          :field="fields.level"
          :sortField="fields.levelSort"
          sortable
          style="min-width: 5rem"
          :filterMatchModeOptions="[{ label: 'Equals', value: 'equals' }]"
          :maxConstraints="1"
          :showAddButton="false"
          :showOperator="false"
        >
          <template #header>
            <span
              v-tooltip.top="headers.level"
              v-help.top="{
                title: 'Identification level',
                helpKey: 'assignment-level',
                doc: app.ui.help.docUrl('how-it-works/peak-assignment/#identification-level')
              }"
              >Level</span
            >
          </template>
          <template #body="{ data }"><slot name="level" :data="data" /></template>
          <template #filter="{ filterModel, filterCallback }">
            <Select
              v-model="filterModel.value"
              @change="filterCallback()"
              :options="LEVEL_FILTER_OPTIONS"
              optionLabel="label"
              optionValue="value"
              placeholder="Any level"
              size="small"
              :showClear="true"
            >
              <template #option="{ option }">
                <span class="level-option"
                  ><b>{{ option.label }}</b> {{ option.name }}</span
                >
              </template>
            </Select>
          </template>
        </Column>

        <slot name="end-columns" />

        <!-- Sorts on the rank the scope gives each row's verdict, confirmed
             first and unjudged last, as the tier column sorts on its rank. -->
        <Column field="verdictRank" sortable style="min-width: 3.5rem">
          <template #header>
            <span
              class="pi ph ph-seal-check"
              v-tooltip.top="headers.verdict"
              v-help.top="verdictHelp"
            />
          </template>
          <template #body="{ data }">
            <LedgerVerdictCell
              v-bind="verdictCell(data)"
              @open="$emit('open-verdict', $event, data)"
            />
          </template>
        </Column>
      </DataTable>

      <slot name="popovers" />
    </div>
  </BaseTabbedPanel>
</template>

<style scoped>
.ledger-count {
  margin-right: 0.6rem;
  font-size: 0.8rem;
  font-variant-numeric: tabular-nums;
  color: var(--p-text-muted-color);
}

/* The panel body is a column: the banners and the tier strip take their
   natural height and the table takes the rest, so whatever is shown above the
   table shortens it instead of pushing it past the pane. */
:deep(.p-panel-content) {
  display: flex;
  flex-direction: column;
  min-height: 0;
}
:deep(.p-message) {
  flex: 0 0 auto;
}
.ledger {
  flex: 1;
  min-height: 0;
  /* .col centres on space-between, which would drop the spinner and the load
     error to the bottom of the pane now that this column has room to spare. */
  justify-content: flex-start;
}
.ledger > :deep(.p-datatable) {
  flex: 1;
  min-height: 0;
}
.ledger > :deep(.p-datatable > .p-datatable-table-container) {
  flex: 1;
  min-height: 0;
}
.loading-region {
  width: 100%;
  min-height: 10rem;
}

.strip-end {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  margin-left: auto;
}
/* The view-options panel: one labelled setting per row. */
.view-menu {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  min-width: 14rem;
}

.intensity {
  font-variant-numeric: tabular-nums;
}
/* A level filter's option: the level, then its name, the levels aligned. */
.level-option b {
  display: inline-block;
  min-width: 1.6rem;
}

/* The formula cell, in either scope: formulas in Plex Mono. */
.ledger :deep(.formula) {
  font-family: var(--font-mono);
}
/* The copy button sits between the formula and its markers in source order,
   and it reserves its space even while hidden - which would strand the "+N" a
   button's width away from the formula it counts for. Ordering it last in the
   flex row puts the markers back against the formula. */
.ledger :deep(.formula button) {
  order: 1;
}
.ledger :deep(.iso-count) {
  margin-left: 0.35rem;
  font-family: var(--font-mono);
  font-size: 0.62rem;
  opacity: 0.55;
  vertical-align: super;
  /* Its parent is BaseCopyableField's flex row, where vertical-align does
     nothing; align to the top edge to keep the superscript reading. */
  align-self: flex-start;
}
/* An unfolded isotopologue line: indented, named by its substitution. */
.ledger :deep(.child-cell) {
  display: inline-flex;
  align-items: baseline;
  gap: 0.35rem;
  padding-left: 0.9rem;
}
.ledger :deep(.child-caret) {
  opacity: 0.4;
}
.ledger :deep(.child-label) {
  font-family: var(--font-mono);
  font-size: 0.86rem;
  opacity: 0.8;
}
</style>
