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

      <p>Click on a data point to visualize the corresponding Match.</p>

      <p>
        Chart settings (top-left) allow you to select the x-axis dimension, y-axis scaling etc.
      </p>
    `
  },
  {
    label: 'Sample',
    icon: 'pi pi-chart-bar',
    disabled: !app.data.sample.focused,
    doc: app.ui.help.docUrl('how-it-works/peak-detection/'),
    help: `
      <h1>Sample View</h1>

      <p>Visualize the selected sample's spectrum and peaks.</p>

      <p>Assign elemental composition to detected peaks and add them to a target collection.</p>
    `
  },
  {
    // Renamed to "Fit" only where peak-centric assignment is on: there the
    // Sample view owns assignment and this tab is purely fit verification.
    // Without the feature it is the long-standing Match tab.
    label: peakAssignmentEnabled ? 'Fit' : 'Match',
    // Internal tab value stays 'match' so existing app.ui.tab.active === 'match'
    // callers and the TabPanel value keep working; only the label is renamed.
    value: 'match',
    icon: 'pi pi-verified',
    disabled: !app.data.match.visualized.ion,
    // Card body comes from the shared docs snippet (docs/user/_help/matching.md)
    // rather than inline prose -- the docs are the single source of truth.
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
            <TabList>
              <Tab
                v-for="{ icon, label, value, disabled, help, doc, helpKey, title } in tabs"
                :value="value ?? label.toLowerCase()"
                :key="label"
                :disabled="disabled"
                :pt="app.ui.help.bottom(help ?? { helpKey, title }, doc ? { doc } : undefined)"
              >
                <div class="row">
                  <span :class="icon" /><span>{{ label }}</span>
                </div>
              </Tab>
            </TabList>
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
  grid-area: charts;
  border: none;
  height: calc(100% -10rem);
}

#charts :deep(.p-panel-header) {
  display: none;
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
