<script setup>
import { computed } from 'vue'

import Splitter from 'primevue/splitter'
import SplitterPanel from 'primevue/splitterpanel'
import Panel from 'primevue/panel'
import Tabs from 'primevue/tabs'
import TabList from 'primevue/tablist'
import Tab from 'primevue/tab'
import TabPanels from 'primevue/tabpanels'
import TabPanel from 'primevue/tabpanel'

import { ToolbarAppFilters } from '@/lib/toolbars'
import { peakAssignmentEnabled } from '@/lib/features'
import {
  PaneProgress,
  PaneBrowserSample,
  PaneBrowserMatch,
  PaneTabMatch,
  PaneTabAcquisitions,
  PaneTabSample,
  PaneTabBatch
} from '@/lib/panes'
import { HelpButton, HelpPopover, DocsButton } from '@/lib/help'

import { useApp } from '@/stores'

const app = useApp()

// A disabled tab keeps its pointer events (see the style below) so that its
// tooltip can say what enables it. PrimeVue's Tab would still take the click
// and the Enter it was never meant to get, so both are stopped on the way in,
// at the tab list, before they reach it - and the mousedown, which is what
// would focus the tab. Only the keys that activate a tab are stopped: a tab
// can hold focus as it becomes disabled, and swallowing Tab there would keep
// the focus in the tab list.
const ACTIVATION_KEYS = ['Enter', 'NumpadEnter', 'Space']
const guardDisabledTab = (event) => {
  if (event.type === 'keydown' && !ACTIVATION_KEYS.includes(event.code)) return
  if (event.target?.closest?.('[data-p-disabled="true"]')) {
    event.stopPropagation()
    event.preventDefault()
  }
}

// `reason` is what a disabled tab says: what would enable it. In a tooltip
// for the pointer, and as the tab's accessible description for everyone else.
const reasonId = (tab) => `tab-reason-${(tab.value ?? tab.label).toLowerCase()}`
const tabs = computed(() => [
  {
    label: 'Raw files',
    icon: 'pi pi-hourglass',
    doc: app.ui.help.docUrl('guides/import-files/'),
    help: `
      <h1>Raw files</h1>
      <p>
        View data files uploaded to the Mascope server, and import files from your computer.
        Uploaded files are processed automatically into the instrument's acquisition workspace.
      </p>
      <p>
        Process files retrospectively into samples and batches.
      </p>
    `
  },
  {
    label: 'Batch',
    icon: 'pi pi-hashtag',
    disabled: !app.data.batch.focused || app.data.sample.list.length === 0,
    reason: 'Open a batch with samples',
    doc: app.ui.help.docUrl('guides/target-collections/#read-the-results'),
    help: `
      <h1>Batch View</h1>

      <p>
        Visualize the currently selected batch. Select a target 
        collection to view the matches found in each sample in the batch.
      </p>

      <p>
        Drag with mouse to zoom in, or pick the Select-tool to select samples by
        dragging instead. Chart tools are available at top-right. Double click to reset zoom.
      </p>  

      ${
        peakAssignmentEnabled
          ? '<p>The <b>Targets</b> / <b>Assignments</b> switch above the browser decides what this chart plots: target matches, or the batch peaks you select in the ledger.</p>'
          : '<p>Click on a data point to visualize the corresponding Match.</p>'
      }

      <p>
        Chart settings (top-left) allow you to select the x-axis dimension, y-axis scaling etc.
      </p>
    `
  },
  {
    label: 'Sample',
    icon: 'pi pi-chart-bar',
    disabled: !app.data.sample.focused,
    reason: 'Select a sample',
    doc: app.ui.help.docUrl(
      peakAssignmentEnabled ? 'how-it-works/peak-assignment/' : 'how-it-works/peak-detection/'
    ),
    help: `
      <h1>Sample View</h1>

      <p>Visualize the selected sample's spectrum and peaks.</p>

      ${
        peakAssignmentEnabled
          ? "<p>Inspect each peak's committed assignment, verify it, and re-search compositions on demand.</p>"
          : '<p>Assign elemental composition to detected peaks and add them to a target collection.</p>'
      }
    `
  },
  // The Match tab renders whatever the peak_assignment flag says: the two
  // paradigms coexist rather than one replacing the other, so turning
  // assignment on must not take the targeted workflow's per-ion view away with
  // it. It is the only home of the match-parameter drawer and the Rate Match
  // action, which the Sample view does not carry - retiring it would have
  // removed both from every deployment (it previously did; see
  // docs/dev/peak_assignment_frontend.md, F6 / issue #1736). The tab stays
  // enabled only once an ion is visualized, so it costs an assignment-first
  // user nothing.
  {
    label: 'Match',
    // Internal tab value stays 'match' to match the TabPanel value and
    // the existing app.ui.tab.active === 'match' callers.
    value: 'match',
    icon: 'pi pi-verified',
    disabled: !app.data.match.visualized.ion,
    reason: 'Open a match from the ion table',
    // Card body comes from the shared docs snippet
    // (docs/user/_help/matching.md) rather than inline prose -- the docs
    // are the single source of truth.
    title: 'Match view',
    helpKey: 'matching',
    doc: app.ui.help.docUrl('how-it-works/matching/')
  }
])
</script>

<template>
  <article style="position: relative">
    <menu id="filters">
      <ToolbarAppFilters />
    </menu>
    <div class="help-dock" style="position: absolute; top: 80px; right: 1rem; z-index: 100">
      <HelpButton />
      <DocsButton />
    </div>
    <Splitter
      style="grid-area: dashboard; height: 100%"
      stateStorage="local"
      stateKey="mascope-dashboard-split"
      @resize="
        ({ sizes }) => {
          app.ui.split.left = sizes[0]
          app.ui.split.right = sizes[1]
        }
      "
    >
      <SplitterPanel :size="app.ui.split.left" :minSize="10">
        <Splitter
          layout="vertical"
          stateStorage="local"
          stateKey="mascope-browser-split"
          @resize="
            ({ sizes }) => {
              app.ui.split.top = sizes[0]
              app.ui.split.bottom = sizes[1]
            }
          "
        >
          <SplitterPanel :size="app.ui.split.top" :minSize="10">
            <PaneBrowserSample />
          </SplitterPanel>
          <SplitterPanel :size="app.ui.split.bottom" :minSize="10">
            <PaneBrowserMatch />
          </SplitterPanel>
        </Splitter>
      </SplitterPanel>
      <SplitterPanel :size="app.ui.split.right" :minSize="10">
        <Panel id="charts">
          <Tabs v-model:value="app.ui.tab.active">
            <TabList
              @click.capture="guardDisabledTab"
              @mousedown.capture="guardDisabledTab"
              @keydown.capture="guardDisabledTab"
            >
              <!-- Rendered as a div rather than PrimeVue's default button: a
                   disabled button gets no hover in every browser, so it could
                   never show the reason it is disabled. The role and keyboard
                   handling are PrimeVue's own either way; the disabled state
                   is not, since Tab only puts it on a button, so it is said
                   here, and the reason is given after the list as the tab's
                   description, for a reader the tooltip never reaches. -->
              <Tab
                v-for="tab in tabs"
                :value="tab.value ?? tab.label.toLowerCase()"
                :key="tab.label"
                :disabled="tab.disabled"
                as="div"
                :aria-disabled="tab.disabled || undefined"
                :aria-describedby="tab.disabled ? reasonId(tab) : undefined"
                v-tooltip.bottom="tab.disabled ? tab.reason : null"
                :pt="
                  app.ui.help.bottom(
                    tab.help ?? { helpKey: tab.helpKey, title: tab.title },
                    tab.doc ? { doc: tab.doc } : undefined
                  )
                "
              >
                <div class="row">
                  <span :class="tab.icon" /><span>{{ tab.label }}</span>
                </div>
              </Tab>
            </TabList>
            <!-- What a disabled tab is described by. Outside the tabs: text
                 inside one joins the tab's accessible name, so the reason
                 would be read as part of the name and again as the
                 description. A hidden element still serves aria-describedby. -->
            <span
              v-for="tab in tabs.filter((tab) => tab.disabled)"
              :key="tab.label"
              :id="reasonId(tab)"
              hidden
              >{{ tab.reason }}</span
            >
            <TabPanels>
              <TabPanel value="raw files">
                <PaneTabAcquisitions :active="app.ui.tab.active == 'raw files'" />
              </TabPanel>
              <TabPanel value="batch">
                <PaneTabBatch />
              </TabPanel>
              <TabPanel value="sample" :pt="{ content: { style: { padding: 0 } } }">
                <PaneTabSample />
              </TabPanel>
              <TabPanel value="match" :pt="{ content: { style: { padding: 0 } } }">
                <PaneTabMatch />
              </TabPanel>
            </TabPanels>
          </Tabs>
        </Panel>
      </SplitterPanel>
    </Splitter>
    <PaneProgress />
    <HelpPopover />
  </article>
</template>

<style scoped>
article {
  width: 100%;
  max-width: 100%;
  height: 100vh;
  max-height: 100vh;
  display: grid;
  grid-template-rows: 60px calc(100vh - 65px - 2rem) 5px;
  grid-template-columns: 1fr;
  grid-template-areas:
    'filters'
    'dashboard'
    'progress';
  gap: 0.5rem;
  padding: 0.5rem;
  overflow: hidden;
}

#filters {
  grid-area: filters;
}
.help-dock {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 0.25rem;
}
#charts {
  border: none;
}

#charts :deep(.p-panel-header) {
  display: none;
}

/* A disabled tab answers a hover with what enables it. PrimeVue's base style
   turns pointer events off on anything .p-disabled, which would keep that
   tooltip from ever opening; the click it lets through is stopped in the
   tab list (guardDisabledTab). */
#charts :deep(.p-tab.p-disabled) {
  pointer-events: auto;
  cursor: not-allowed;
}

menu {
  padding: 0;
  margin: 0;
}
menu > :deep(*) {
  height: 60px;
}

/* Prevent content from expanding the splitter beyond article width */
article :deep(.p-splitter),
article :deep(.p-splitterpanel),
article :deep(.p-panel-content-container),
article :deep(.p-panel-content-wrapper),
article :deep(.p-panel-content),
article :deep(.p-tabs),
article :deep(.p-tabpanels),
article :deep(.p-tabpanel) {
  min-width: 0;
}
</style>
