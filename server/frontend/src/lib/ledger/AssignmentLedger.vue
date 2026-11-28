<script setup>
import { computed, onBeforeUnmount, ref, useAttrs, watch } from 'vue'

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
import LedgerMenuChoice from './LedgerMenuChoice.vue'
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
 * - the narrow widths: ./narrow.css drops the Level column, the sample
 *   count, the batch's badges and the intensity's bar, in that order. The
 *   level itself stays: where its column drops, its chips move into the Tier
 *   cell. Show carries the level's filter and sort, so they stay within
 *   reach with the column gone. With the badges, the tier chip's word goes
 *   from sight, leaving its dot; with the bar, the Intensity column narrows
 *   to its number;
 * - the lens (section 3.5): with `lens` set, a bar above the table names the
 *   group the rows are narrowed to, offers to plot it where the scope can,
 *   and closes with its x or Escape; a Step or Channel column labels each
 *   row (`lensLabel`);
 * - the rows that stand for no peak of the scope (`ledgerKind`): a group
 *   line of Group by, which folds its members (`toggle-group`), a series
 *   step with no batch peak (`gap`), a member the scope holds no row for
 *   (`absent`), and a row listed under another group line (`reference`).
 *   Each shows its label in the Compound cell and nothing a row of a peak
 *   would: no box, tier, verdict or action.
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
  /**
   * The Level filter's options: the scale's, and a version-1 claim's bare
   * number where a row shows one (levelFilterOptions).
   */
  levelOptions: { type: Array, default: () => LEVEL_FILTER_OPTIONS },
  /** Header tooltips by column key. */
  headers: { type: Object, default: () => ({}) },
  intensityLabel: { type: String, default: 'Intensity' },
  /**
   * The Intensity header where the ledger is narrow enough to drop the bar
   * (./narrow.css): short, so the header is no wider than the number. The
   * full label stays its accessible name and its hover.
   */
  intensityShortLabel: { type: String, default: 'Int.' },
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
  verdictHelp: { type: Object, required: true },

  /**
   * The lens the rows are narrowed to, or null: `title`, the bar's line;
   * `column`, the label column's header (Step or Channel); `plot`, whether
   * the bar offers to plot the group (`plot-lens`).
   */
  lens: { type: Object, default: null }
})
const emit = defineEmits([
  'update:sortField',
  'update:sortOrder',
  'update:search',
  'toggle-tier',
  'open-verdict',
  'open-mz',
  'toggle-family',
  'toggle-group',
  'close-lens',
  'plot-lens'
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
// A row that stands for no peak of the scope: a group line, a gap, a member
// the scope holds no row for, or a row listed under an earlier group line.
const isPseudo = (row) => Boolean(row?.ledgerKind)
// A row that shows only what a line or a pseudo-row shows.
const isQuiet = (row) => isLineRow(row) || isPseudo(row)

// A line is a sub-row: the class the narrow rules and the quiet style key on,
// beside whatever the scope gives its rows.
const rowClass = (row) => {
  const own = typeof attrs.rowClass === 'function' ? attrs.rowClass(row) : attrs.rowClass
  return [
    own,
    isLineRow(row) ? 'ledger-line' : null,
    isPseudo(row) ? `ledger-pseudo ledger-${row.ledgerKind}` : null
  ].filter(Boolean)
}

// Escape closes the lens, wherever the focus is, but not while a menu or a
// dialog over the ledger is open: Escape is theirs first.
function onLensKey(event) {
  if (event.key !== 'Escape' || event.defaultPrevented || !props.lens) return
  const target = event.target
  if (target?.closest?.('.p-popover, .p-dialog, .p-select-overlay')) return
  emit('close-lens')
}
watch(
  () => Boolean(props.lens),
  (open) => {
    if (open) window.addEventListener('keydown', onLensKey)
    else window.removeEventListener('keydown', onLensKey)
  },
  { immediate: true }
)
onBeforeUnmount(() => window.removeEventListener('keydown', onLensKey))

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

// The level's filter and sort in Show, for the widths that drop its column.
// The filter is the Level column's own menu filter (the DataTable's
// `filters`, which the scope binds with v-model): read off the attrs it
// passes and written back through the same update, so the column's menu and
// Show never disagree. The attrs are read in the render, not in a computed:
// Vue updates them on the parent's render, but tracks no change to them.
const levelChoices = computed(() =>
  props.levelOptions.map((option) => ({
    value: option.value,
    label: option.label,
    tooltip: option.name
  }))
)
const levelFilterValue = () => attrs.filters?.[props.fields.level]?.constraints?.[0]?.value ?? null
function setLevelFilter(value) {
  const filters = attrs.filters
  const meta = filters?.[props.fields.level]
  if (!meta) return
  const [first, ...rest] = meta.constraints
  attrs['onUpdate:filters']?.({
    ...filters,
    [props.fields.level]: { ...meta, constraints: [{ ...first, value }, ...rest] }
  })
}
// Ascending rank reads most identified first (lib/levels.js, LEVEL_RANK).
const LEVEL_SORTS = [
  { value: 1, label: 'Strongest first', tooltip: 'Confirmed structure (1) first' },
  { value: -1, label: 'Weakest first', tooltip: 'A measured mass (5) first' }
]
const levelSortValue = computed(() =>
  props.sortField === props.fields.levelSort ? props.sortOrder : null
)
function setLevelSort(order) {
  emit('update:sortField', order == null ? null : props.fields.levelSort)
  emit('update:sortOrder', order)
}

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
            <div class="view-menu">
              <slot name="view-menu" />
              <!-- The level's filter and sort, the Level column's own, here
                   too: at a narrow width the column drops (./narrow.css),
                   its header with it, and the level's chips move into the
                   Tier cell. One filter either way: both write the same
                   column filter. -->
              <div v-if="hasLevels" class="menu-section level-menu">
                <span class="menu-heading">Level</span>
                <LedgerMenuChoice
                  :modelValue="levelFilterValue()"
                  class="level-filter"
                  label="Only the level"
                  ariaLabel="Filter by identification level"
                  :options="levelChoices"
                  clearable
                  @update:modelValue="setLevelFilter"
                />
                <LedgerMenuChoice
                  :modelValue="levelSortValue"
                  class="level-sort"
                  label="Sort by level"
                  ariaLabel="Sort the rows by identification level"
                  :options="LEVEL_SORTS"
                  clearable
                  @update:modelValue="setLevelSort"
                />
              </div>
            </div>
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

      <!-- The lens: the group the rows are narrowed to, the way to plot it,
           and the way out. Escape closes it too. -->
      <div v-if="lens" class="lens-bar" role="region" aria-label="Group" data-testid="lens-bar">
        <span class="pi ph ph-funnel-simple lens-icon" aria-hidden="true" />
        <span class="lens-title" data-testid="lens-title">{{ lens.title }}</span>
        <Button
          v-if="lens.plot"
          class="lens-plot"
          label="Plot the group"
          icon="pi ph ph-chart-line"
          size="small"
          text
          data-testid="lens-plot"
          v-tooltip.top="
            'Plot the group\'s batch peaks in the batch chart, in place of what it plots'
          "
          @click="$emit('plot-lens')"
        />
        <Button
          class="lens-close"
          icon="pi pi-times"
          size="small"
          text
          rounded
          severity="secondary"
          aria-label="Close the group (Escape)"
          data-testid="lens-close"
          v-tooltip.top="'Close the group and list the ledger as before (Escape)'"
          @click="$emit('close-lens')"
        />
      </div>

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

        <!-- Under the lens: each row's place in the group, its step from the
             series' root or the channel it reads. -->
        <Column v-if="lens" field="lensLabel" style="min-width: 5rem">
          <template #header>
            <span>{{ lens.column }}</span>
          </template>
          <template #body="{ data }">
            <span v-if="data.lensLabel" class="lens-label" data-testid="lens-label">{{
              data.lensLabel
            }}</span>
          </template>
        </Column>

        <!-- The header tooltips sit on a span in the header slot, never on the
             Column itself: Column renders no element of its own. -->
        <Column :field="fields.mz" sortable style="min-width: 5.5rem">
          <template #header>
            <span v-tooltip.top="headers.mz">m/z</span>
          </template>
          <!-- The peak, in the inspector: the sample's own row, or the batch
               peak in its brightest sample. -->
          <template #body="{ data }">
            <span v-if="isPseudo(data)" class="pseudo-mz">{{
              data.pseudoMz != null ? num.mz.format(data.pseudoMz) : ''
            }}</span>
            <button
              v-else
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
            <!-- A group line of Group by: its name, its member count and its
                 best member's tier. A button, so Tab reaches it and Enter or
                 Space fold it; the keys stop here, where the table would read
                 them as a selection. -->
            <button
              v-if="data.ledgerKind === 'group'"
              type="button"
              class="group-line"
              data-testid="group-line"
              :aria-expanded="!data.collapsed"
              :aria-label="`${data.collapsed ? 'Unfold' : 'Fold'} ${data.groupName}, ${data.groupCount}`"
              @click.stop="$emit('toggle-group', data)"
              @keydown.enter.stop
              @keydown.space.stop
            >
              <span
                :class="['pi', data.collapsed ? 'pi-chevron-right' : 'pi-chevron-down']"
                aria-hidden="true"
              />
              <span class="group-name">{{ data.groupName }}</span>
              <span class="group-count">{{ data.groupCount }}</span>
            </button>
            <!-- A gap, a member the scope holds no row for, or a row listed
                 under an earlier group line: greyed, with what it is. -->
            <span
              v-else-if="isPseudo(data)"
              class="pseudo-cell"
              :data-testid="`pseudo-${data.ledgerKind}`"
            >
              <span v-if="data.pseudoFormula" class="formula">{{ data.pseudoFormula }}</span>
              <span class="pseudo-note">{{ data.pseudoNote }}</span>
            </span>
            <span v-else-if="isLineRow(data)" class="line-cell" data-testid="line-cell"
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
            <span v-if="isPseudo(data)" class="ion pseudo-ion">{{ data.pseudoIon ?? '' }}</span>
            <span v-else-if="!isLineRow(data)" class="ion-cell"
              ><slot name="ion" :data="data"
                ><span class="ion">{{ data[fields.ion] ?? '' }}</span></slot
              ></span
            >
          </template>
        </Column>

        <!-- Its minimum width is ./narrow.css's (col-intensity), which lowers
             it where the bar drops, as the Tier column's (col-tier). -->
        <Column
          :field="fields.intensity"
          sortable
          headerClass="col-intensity"
          bodyClass="col-intensity"
        >
          <template #header>
            <!-- The full label, and the short one ./narrow.css shows in its
                 place at a narrow width, where the full one goes from sight but
                 stays the header's name. The hover names the label first. -->
            <span v-tooltip.top="headers.intensity || intensityLabel"
              ><span class="intensity-label">{{ intensityLabel }}</span
              ><span class="intensity-label-short" aria-hidden="true">{{
                intensityShortLabel
              }}</span></span
            >
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

        <Column field="tierRank" sortable headerClass="col-tier" bodyClass="col-tier">
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
            <!-- At a narrow width ./narrow.css shows its asterisk in place of
                 its word, which stays the mark's name. -->
            <BaseProvisionalMark collapsible />
          </template>
          <!-- The producing engine's own tier, beside this server's rather than
               instead of it: the tier this server measured is what the table
               sorts, filters and rolls up on, and a row where the two differ is
               the one worth opening. -->
          <template #body="{ data }">
            <!-- A group line: its best member's tier. -->
            <span v-if="data.ledgerKind === 'group'" class="tier-cell">
              <BaseTierTag
                v-if="data.groupTier || data.groupRole"
                :tier="data.groupTier"
                :role="data.groupRole"
              />
            </span>
            <span v-else-if="!isQuiet(data)" class="tier-cell">
              <slot name="tier" :data="data" :cap="tierCap(data)" />
              <BaseTierTag
                v-if="engineReadings && data.engine_tier"
                class="engine-chip"
                data-testid="engine-tier-chip"
                :tier="data.engine_tier"
                :tooltip="engineChipTooltip(data)"
                outlined
              />
              <!-- The level, where the Level column has no room: the same
                   chips the column draws (the engine's own with Show > Engine
                   readings on), compact beside the tier. ./narrow.css shows
                   this copy only at the widths that drop the column, so a row
                   never carries the level twice. -->
              <span v-if="hasLevels" class="tier-level" data-testid="tier-level"
                ><slot name="level" :data="data"
              /></span>
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
            <span v-if="!isQuiet(data)" class="level-cell"><slot name="level" :data="data" /></span>
          </template>
          <template #filter="{ filterModel, filterCallback }">
            <Select
              v-model="filterModel.value"
              @change="filterCallback()"
              :options="levelOptions"
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
              v-if="!isQuiet(data)"
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
            <span v-if="!isQuiet(data)" class="row-actions" data-testid="row-actions"
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

/* The lens's bar: the group's name, Plot the group and the x. */
.lens-bar {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  flex: 0 0 auto;
  padding: 0.2rem 0.4rem 0.2rem 0.6rem;
  border: 1px solid var(--p-content-border-color);
  border-radius: 6px;
  background: var(--p-content-hover-background);
  font-size: 0.82rem;
}
.lens-icon {
  color: var(--p-text-muted-color);
}
.lens-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.lens-label {
  font-family: var(--font-mono);
  font-size: 0.78rem;
  color: var(--p-text-muted-color);
}

/* The rows that stand for no peak: greyed, a gap or an absent member with
   what it is beside its formula. */
.ledger :deep(tr.ledger-gap > td),
.ledger :deep(tr.ledger-absent > td),
.ledger :deep(tr.ledger-reference > td) {
  color: var(--p-text-muted-color);
  font-size: 0.82rem;
}
.ledger :deep(tr.ledger-gap > td) {
  font-style: italic;
}
.pseudo-cell {
  display: inline-flex;
  align-items: baseline;
  gap: 0.4rem;
}
.pseudo-note {
  font-size: 0.72rem;
}
.pseudo-mz {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}
/* A group line of Group by: its caret, its name and its count. */
.ledger :deep(tr.ledger-group > td) {
  background: var(--p-content-hover-background);
}
.group-line {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.1rem 0.3rem;
  border: 0;
  border-radius: 3px;
  background: transparent;
  color: inherit;
  font: inherit;
  font-weight: 500;
  cursor: pointer;
}
.group-line .pi {
  font-size: 0.65rem;
  color: var(--p-text-muted-color);
}
.group-line:focus-visible {
  outline: 2px solid var(--p-primary-color);
}
.group-count {
  font-size: 0.72rem;
  font-weight: 400;
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
/* Under :where(), so the rule weighs less than ./narrow.css's: scoped, it
   weighed as much and came later, and its display won over the rule that
   drops the bar at a narrow width. */
:where(.intensity-bar) {
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
/* The level beside the tier at a narrow width: its chips compact, the
   number and letter in the band's token. No display of its own here:
   ./narrow.css alone says at which widths it shows. */
.tier-level :deep(.level-tag + .level-tag) {
  margin-left: 0.2rem;
}
.tier-level :deep(.level) {
  min-width: 0;
  padding: 0 0.3rem;
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
