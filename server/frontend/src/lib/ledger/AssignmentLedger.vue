<script setup>
import { computed, ref, useAttrs } from 'vue'

import Button from 'primevue/button'
import Column from 'primevue/column'
import DataTable from 'primevue/datatable'
import IconField from 'primevue/iconfield'
import InputIcon from 'primevue/inputicon'
import InputText from 'primevue/inputtext'
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
import { tierName } from '@/lib/tiers'
import { LEVEL_FILTER_OPTIONS } from '@/lib/levels'
import { useApp } from '@/stores'

import LedgerListingCell from './LedgerListingCell.vue'
import LedgerTierStrip from './LedgerTierStrip.vue'
import LedgerVerdictCell from './LedgerVerdictCell.vue'
import LedgerViewToggle from './LedgerViewToggle.vue'
import { compactNumber, intensityBarShare, intensityDomain } from './intensity.js'
import { LEDGER_ROW_HEIGHT } from './rows.js'
import { useLedgerListing } from './useLedgerView.js'
// The widths at which the ledger drops cells, by container query.
import './narrow.css'

/**
 * The assignment ledger: a formula at an m/z with a tier, per peak of a sample
 * or per batch peak of a batch. One component for both scopes; the panes
 * (PaneBrowserAssignment, PaneBrowserBatchPeaks) are its configurations - the
 * rows and how they are read, the scope's extra columns and strip items, the
 * selection - and it draws what the two share, the same way in both:
 *
 * - the header line, the scope's title and its count;
 * - the filter bar (docs/dev/assignment_ledger_and_card.md, section 3.4): the
 *   search box, Show - the scope's menu of what the rows show - and the
 *   overflow menu, with the listing switch both scopes share and the scope's
 *   actions;
 * - the tier bar over its chips, multi-select, with the scope's items at its
 *   right;
 * - one inline spinner, one inline load error and one empty state where the
 *   table would be, so the strip and the bar above stay usable;
 * - the shared columns (section 3.2), sentence case, in this order: the
 *   scope's Plot column, m/z, Compound (the formula, with what a list or a
 *   target names it under it), Ion, Intensity (a compact number and a
 *   log-scale bar), Tier (a capping reason as a mark on its chip), Level where
 *   any row has one, the scope's own columns, the verdict, and the row's
 *   actions. With Show > Engine readings on, an importing engine's own tier
 *   is a second, outlined chip in the Tier cell. A cell with nothing in it is
 *   blank;
 * - the rows (section 3.3): 40 px each. An isotopologue line is a quiet
 *   sub-row under its family's row, showing its label, its m/z and its
 *   intensity, and nothing it would only repeat: no plot box, ion, tier,
 *   level, verdict or action. The row's actions show on hover, on focus
 *   within the row and on the selected row. Clicking an m/z opens the peak
 *   in the inspector (`open-mz`);
 * - the narrow widths: ./narrow.css drops the level, the sample count, the
 *   batch's badges and the intensity's bar, in that order.
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
  sortField: { type: String, default: null },
  sortOrder: { type: Number, default: null },
  /**
   * The row field each shared column reads: `mz`, `intensity`, `formula`,
   * `ion`, `level`, and `levelSort` for the level's rank.
   */
  fields: { type: Object, required: true },
  /** Header tooltips by column key. */
  headers: { type: Object, default: () => ({}) },
  intensityLabel: { type: String, default: 'Intensity' },
  /**
   * The intensities the bars are drawn over: every row the scope holds, not
   * only the ones its filters leave, so a bar keeps its length as the reader
   * narrows the ledger. The rows shown where it is not given.
   */
  intensities: { type: Array, default: null },
  /** What clicking an m/z says it does, for its accessible name. */
  mzAction: { type: String, default: 'Open this peak in the inspector' },

  /** The search box's query, and what it says it searches. */
  search: { type: String, default: '' },
  searchPlaceholder: { type: String, default: 'Formula, name or m/z' },
  searchTooltip: { type: String, default: '' },

  /**
   * The tier strip, and whether it is drawn at all. It is drawn while `error`
   * is set whatever `strip` says, with no counts on its chips.
   */
  strip: { type: Boolean, default: true },
  chips: { type: Array, required: true },
  activeTiers: { type: Set, required: true },
  engineView: { type: Boolean, default: false },
  engineViewTooltip: { type: String, default: '' },
  /** Show, the scope's view menu: its tooltip and help card. */
  viewMenuTooltip: { type: String, default: '' },
  viewMenuPt: { type: Object, default: null },

  /**
   * Show > Engine readings: an importing engine's own tier as a second,
   * outlined chip in the Tier cell, where a row carries one
   * (`engine_tier`). Owner's decision of 2026-11-22: the chip, not a column
   * of its own.
   */
  engineReadings: { type: Boolean, default: false },
  engineTierTooltip: { type: Function, default: () => '' },
  hasLevels: { type: Boolean, default: false },
  /** The cap mark on a row's tier chip (BaseTierTag's `cap`), or null. */
  tierCap: { type: Function, default: () => null },
  /** How many isotopologue lines fold under a row, and whether they are shown. */
  familySize: { type: Function, default: () => 0 },
  familyOpen: { type: Function, default: () => false },

  /** The verdict cell's props for a row (LedgerVerdictCell), and its help card. */
  verdictCell: { type: Function, required: true },
  verdictHelp: { type: Object, required: true }
})
defineEmits([
  'update:sortField',
  'update:sortOrder',
  'update:search',
  'toggle-tier',
  'open-verdict',
  'open-mz',
  'toggle-family'
])
const attrs = useAttrs()

const app = useApp()
const listingColumn = useLedgerListing()

const VIEW_MENU_IDS = { sample: 'assignment-view-menu', batch: 'batch-peak-view-menu' }
const viewMenu = ref()
const viewMenuOpen = ref(false)
const toggleViewMenu = (event) => viewMenu.value?.toggle(event)

const OVERFLOW_MENU_IDS = { sample: 'assignment-overflow-menu', batch: 'batch-peak-overflow-menu' }
const overflowMenu = ref()
const overflowMenuOpen = ref(false)
const toggleOverflowMenu = (event) => overflowMenu.value?.toggle(event)

// The table, for a scope that scrolls a row into view.
const table = ref()
defineExpose({ table, viewMenu, overflowMenu })

// An isotopologue line, in either scope: the sample's rows mark it
// `isChild`, the batch's carry the family they fold under as `parentId`.
const isLineRow = (row) => Boolean(row.isChild || row.parentId)

// A line is a sub-row: the class the narrow rules and the quiet style key on,
// beside whatever the scope gives its rows.
const rowClass = (row) => {
  const own = typeof attrs.rowClass === 'function' ? attrs.rowClass(row) : attrs.rowClass
  return [own, isLineRow(row) ? 'ledger-line' : null].filter(Boolean)
}

// The intensity: the compact number, the exact one on hover, and the bar on a
// log scale over the scope's rows.
const domain = computed(() =>
  intensityDomain(props.intensities ?? props.rows.map((row) => row[props.fields.intensity]))
)
const intensityOf = (row) => compactNumber(row[props.fields.intensity])
const intensityExact = (row) => {
  const value = row[props.fields.intensity]
  return value != null ? num.peakIntensity.format(value) : ''
}
const barStyle = (row) => ({
  width: `${(intensityBarShare(row[props.fields.intensity], domain.value) * 100).toFixed(1)}%`
})

const engineChipTooltip = (row) =>
  props.engineTierTooltip(row) || `The importing engine's own tier: ${tierName(row.engine_tier)}`
</script>

<template>
  <BaseTabbedPanel :label="label" :icon="icon" :pt="panelPt">
    <template #menu>
      <!-- Not while the rows failed to load: the scope may still hold the last
           good load's rows, and their count would read as this one's. -->
      <span
        v-if="count && !error"
        class="ledger-count"
        data-testid="ledger-count"
        v-tooltip.top="countTooltip"
        >{{ count }}</span
      >
      <!-- The header line's right, after the count: room for the scope's
           control of where it stands (the batch's pass, the sample's run). -->
      <span v-if="$slots['header-end']" class="header-end"><slot name="header-end" /></span>
    </template>

    <!-- What the scope reports above everything: a refused launch. -->
    <slot name="notices" />

    <!-- A state of the scope with no ledger to show (no sample, no runs). -->
    <slot v-if="$slots.state" name="state" />

    <div v-else class="col ledger" style="gap: 0.6rem; align-items: stretch">
      <slot name="above-strip" />

      <!-- The filter bar. Drawn whenever the strip is, a failed load
           included, so the search and the menus stay usable. -->
      <div v-if="strip || error" class="filter-bar">
        <IconField class="ledger-search">
          <InputIcon><i class="pi pi-search" /></InputIcon>
          <InputText
            :modelValue="search"
            type="search"
            size="small"
            :placeholder="searchPlaceholder"
            aria-label="Search the ledger: a formula, a listed name or an m/z"
            data-testid="ledger-search"
            v-tooltip.top="searchTooltip"
            @update:modelValue="$emit('update:search', $event ?? '')"
          />
        </IconField>
        <span class="bar-end">
          <Button
            class="view-menu-button"
            label="Show"
            icon="pi ph ph-funnel"
            severity="secondary"
            text
            size="small"
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
            aria-label="Show"
            @show="viewMenuOpen = true"
            @hide="viewMenuOpen = false"
          >
            <div class="view-menu"><slot name="view-menu" /></div>
          </Popover>
          <Button
            class="overflow-button"
            icon="pi ph ph-dots-three"
            severity="secondary"
            text
            size="small"
            aria-label="More ledger actions"
            aria-haspopup="dialog"
            :aria-controls="overflowMenuOpen ? OVERFLOW_MENU_IDS[scope] : undefined"
            :aria-expanded="overflowMenuOpen"
            v-tooltip.top="'More: export, listed names in full'"
            @click="toggleOverflowMenu"
          />
          <Popover
            ref="overflowMenu"
            :id="OVERFLOW_MENU_IDS[scope]"
            aria-label="More ledger actions"
            @show="overflowMenuOpen = true"
            @hide="overflowMenuOpen = false"
          >
            <div class="view-menu overflow-menu">
              <slot name="overflow" />
              <LedgerViewToggle
                v-model="listingColumn.full"
                :id="`${scope}-listing-full`"
                label="Write listed names in full"
                tooltip="Write out what a reference list calls each formula in full, with its list, rather than the name cut short"
                :autofocus="!$slots.overflow"
              />
            </div>
          </Popover>
        </span>
      </div>

      <!-- Drawn on a failed load too, the first one included, so the chips
           stay usable; its chips then show no number, for the same reason the
           header shows no count. -->
      <LedgerTierStrip
        v-if="strip || error"
        :chips="chips"
        :counted="!error"
        :active="activeTiers"
        :engineView="engineView"
        :engineTooltip="engineViewTooltip"
        @toggle="$emit('toggle-tier', $event)"
      >
        <!-- The scope's own items, at the strip's right. -->
        <slot v-if="$slots.strip" name="strip" />
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
        :rowClass="rowClass"
        size="small"
        scrollable
        scrollHeight="flex"
        :virtualScrollerOptions="{ itemSize: LEDGER_ROW_HEIGHT }"
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

        <!-- Plot: the scope's box that puts the row's species in the batch
             chart. -->
        <slot name="lead-column" />

        <!-- The header tooltips sit on a span in the header slot, never on the
             Column itself: Column renders no element of its own. -->
        <Column :field="fields.mz" sortable style="min-width: 5.5rem">
          <template #header>
            <span v-tooltip.top="headers.mz">m/z</span>
          </template>
          <!-- The peak, in the inspector: the sample's own row, or the batch
               peak in its brightest sample. -->
          <template #body="{ data }">
            <button
              type="button"
              class="mz-link"
              data-testid="ledger-mz"
              :aria-label="`${num.mz.format(data[fields.mz])}: ${mzAction}`"
              @click.stop="$emit('open-mz', data)"
            >
              {{ num.mz.format(data[fields.mz]) }}
            </button>
          </template>
        </Column>

        <!-- Compound: the formula, with what a list or a target names it on a
             second line. A line is named by its substitution instead: it is
             its family's formula again. -->
        <Column :field="fields.formula" sortable style="min-width: 8rem">
          <template #header>
            <span v-tooltip.top="headers.formula">Compound</span>
          </template>
          <template #body="{ data }">
            <span v-if="isLineRow(data)" class="line-cell" data-testid="line-cell"
              ><slot name="line-label" :data="data"
            /></span>
            <span v-else class="compound-cell" data-testid="compound-cell">
              <span class="compound-formula">
                <!-- The family's caret and its line count: folds or unfolds
                     this family's isotopologue lines under the row. -->
                <button
                  v-if="familySize(data)"
                  type="button"
                  class="family-caret"
                  data-testid="family-caret"
                  :aria-expanded="familyOpen(data)"
                  :aria-label="`${familyOpen(data) ? 'Fold' : 'Unfold'} ${familySize(data)} isotopologue line${familySize(data) === 1 ? '' : 's'}`"
                  v-tooltip.top="
                    `${familySize(data)} isotopologue line${familySize(data) === 1 ? '' : 's'}: click to ${familyOpen(data) ? 'fold' : 'unfold'}`
                  "
                  @click.stop="$emit('toggle-family', data)"
                >
                  <span
                    :class="['pi', familyOpen(data) ? 'pi-chevron-down' : 'pi-chevron-right']"
                    aria-hidden="true"
                  />{{ familySize(data) }}
                </button>
                <slot name="formula" :data="data" />
              </span>
              <!-- Draws nothing for a row no list or target names. -->
              <LedgerListingCell
                class="compound-listing"
                :listing="data.listing"
                :target="data.target"
              />
            </span>
          </template>
        </Column>

        <!-- Ion: what tells two channels of one neutral apart. -->
        <Column :field="fields.ion" sortable style="min-width: 5rem">
          <template #header>
            <span v-tooltip.top="headers.ion">Ion</span>
          </template>
          <template #body="{ data }">
            <span v-if="!isLineRow(data)" class="ion-cell"
              ><slot name="ion" :data="data"
                ><span class="ion">{{ data[fields.ion] ?? '' }}</span></slot
              ></span
            >
          </template>
        </Column>

        <Column :field="fields.intensity" sortable style="min-width: 7rem">
          <template #header>
            <span v-tooltip.top="headers.intensity">{{ intensityLabel }}</span>
          </template>
          <template #body="{ data }">
            <span
              v-if="intensityOf(data)"
              class="intensity-cell"
              data-testid="intensity-cell"
              v-tooltip.top="intensityExact(data)"
            >
              <span class="intensity">{{ intensityOf(data) }}</span>
              <span class="intensity-bar" aria-hidden="true"
                ><span class="intensity-fill" :style="barStyle(data)"
              /></span>
            </span>
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
          <!-- The producing engine's own tier, beside this server's rather than
               instead of it: the tier this server measured is what the table
               sorts, filters and rolls up on, and a row where the two differ is
               the one worth opening. -->
          <template #body="{ data }">
            <span v-if="!isLineRow(data)" class="tier-cell">
              <slot name="tier" :data="data" :cap="tierCap(data)" />
              <BaseTierTag
                v-if="engineReadings && data.engine_tier"
                class="engine-chip"
                data-testid="engine-tier-chip"
                :tier="data.engine_tier"
                :tooltip="engineChipTooltip(data)"
                outlined
              />
            </span>
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
          headerClass="col-level"
          bodyClass="col-level"
          style="min-width: 4.5rem"
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
          <template #body="{ data }">
            <span v-if="!isLineRow(data)" class="level-cell"
              ><slot name="level" :data="data"
            /></span>
          </template>
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
        <Column field="verdictRank" sortable style="min-width: 3rem">
          <template #header>
            <span
              class="pi ph ph-seal-check"
              v-tooltip.top="headers.verdict"
              v-help.top="verdictHelp"
            />
          </template>
          <template #body="{ data }">
            <LedgerVerdictCell
              v-if="!isLineRow(data)"
              v-bind="verdictCell(data)"
              @open="$emit('open-verdict', $event, data)"
            />
          </template>
        </Column>

        <!-- The row's actions: shown on hover, on focus within the row and on
             the selected row (the style below), so a keyboard user reaches
             them without selecting the row first. -->
        <Column v-if="$slots.actions" style="width: 2.5rem">
          <template #header>
            <span class="sr-only">Actions</span>
          </template>
          <template #body="{ data }">
            <span v-if="!isLineRow(data)" class="row-actions" data-testid="row-actions"
              ><slot name="actions" :data="data"
            /></span>
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
  /* The narrow rules (./narrow.css) measure the ledger's own width. */
  container: ledger / inline-size;
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

/* The filter bar: the search, then Show and the overflow at the right. */
.filter-bar {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0 0.4rem;
  flex: 0 0 auto;
}
.ledger-search {
  flex: 1 1 12rem;
  max-width: 22rem;
}
.ledger-search :deep(input) {
  width: 100%;
}
.bar-end {
  display: inline-flex;
  align-items: center;
  gap: 0.2rem;
  margin-left: auto;
}
/* A menu's panel: one labelled setting per row, in headed sections. */
.view-menu {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  min-width: 14rem;
}
.view-menu :deep(.menu-section) {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.view-menu :deep(.menu-heading) {
  font-size: 0.68rem;
  font-weight: 500;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--p-text-muted-color);
}

.header-end {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
}

/* Every row 40 px (LEDGER_ROW_HEIGHT, the scroller's item size): the cells
   hold their content on one line, the compound's two excepted. */
.ledger :deep(.p-datatable-tbody > tr > td) {
  box-sizing: border-box;
  height: 40px;
  padding-top: 0;
  padding-bottom: 0;
  white-space: nowrap;
}
/* An isotopologue line: a quiet sub-row, its label, m/z and intensity a step
   smaller and muted. It inherits the rest from the row above it. */
.ledger :deep(tr.ledger-line > td) {
  font-size: 0.82rem;
  color: var(--p-text-muted-color);
}

/* The m/z: the link to the peak, in Plex Mono and tabular, plain until
   hovered. */
.mz-link {
  padding: 0;
  border: 0;
  background: none;
  color: inherit;
  font: inherit;
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  cursor: pointer;
}
.mz-link:hover,
.mz-link:focus-visible {
  color: var(--p-primary-color);
  text-decoration: underline;
}

/* The compound: the formula, and under it what a list calls it, recessive. */
.compound-cell {
  display: inline-flex;
  flex-direction: column;
  justify-content: center;
  min-width: 0;
  line-height: 1.2;
}
.compound-listing {
  font-size: 0.72rem;
  color: var(--p-text-muted-color);
}
.line-cell {
  display: inline-flex;
  align-items: baseline;
}
.ion {
  font-family: var(--font-mono);
  font-size: 0.82rem;
}

/* The intensity: the compact number, then its bar on a log scale. */
.intensity-cell {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
}
.intensity {
  min-width: 2.6rem;
  font-variant-numeric: tabular-nums;
  text-align: right;
}
.intensity-bar {
  display: inline-block;
  width: 3rem;
  height: 4px;
  border-radius: 2px;
  background: var(--p-content-border-color);
  overflow: hidden;
}
.intensity-fill {
  display: block;
  height: 100%;
  background: var(--p-text-muted-color);
}

.tier-cell {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
}

/* The row's actions: out of sight until the row is hovered, focused or
   selected. Transparent rather than hidden, so a keyboard user tabbing
   through the row reaches them. */
.row-actions {
  display: inline-flex;
  opacity: 0;
}
.ledger :deep(tr:hover .row-actions),
.ledger :deep(tr:focus-within .row-actions),
.ledger :deep(tr.p-datatable-row-selected .row-actions) {
  opacity: 1;
}
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
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
/* The family's caret: quiet, its count beside the chevron. */
.family-caret {
  display: inline-flex;
  align-items: center;
  gap: 0.1rem;
  margin-right: 0.3rem;
  padding: 0 0.2rem;
  border: 0;
  border-radius: 3px;
  background: transparent;
  color: var(--p-text-muted-color);
  font: inherit;
  font-family: var(--font-mono);
  font-size: 0.68rem;
  cursor: pointer;
}
.family-caret .pi {
  font-size: 0.6rem;
}
.family-caret:hover {
  background: var(--p-content-hover-background);
}
.compound-formula {
  display: inline-flex;
  align-items: center;
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
