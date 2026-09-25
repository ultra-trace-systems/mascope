<script setup>
/**
 *  Dialog for managing ionization modes and mechanisms
 *
 *  Allows adding and removing modes and mechanisms in separate tabs.
 */
import { watch, ref } from 'vue'

import Dialog from 'primevue/dialog'
import Button from 'primevue/button'
import Tabs from 'primevue/tabs'
import TabList from 'primevue/tablist'
import Tab from 'primevue/tab'
import TabPanels from 'primevue/tabpanels'
import TabPanel from 'primevue/tabpanel'

import PaneIonizationMechanism from '@/lib/panes/PaneIonizationMechanism.vue'
import PaneIonizationMode from '@/lib/panes/PaneIonizationMode.vue'

import { useApp } from '@/stores'

const app = useApp()

const visible = defineModel('visible')
const tab = ref('modes') // Default to modes tab
const layer = 'dialog_ionization' // Help-mode layer for dialog
const modesPaneRef = ref(null)
const mechanismsPaneRef = ref(null)

// reset when modal closed/opened
watch(visible, () => {
  if (modesPaneRef.value) {
    modesPaneRef.value.resetFields()
  }
  if (mechanismsPaneRef.value) {
    mechanismsPaneRef.value.resetFields()
  }
  // Reset to default tab when dialog opens
  if (visible.value) {
    tab.value = 'modes'
    app.ui.help.set(layer)
  } else {
    app.ui.help.set(null)
  }
})
</script>

<template>
  <Dialog
    v-model:visible="visible"
    header="Edit Ionization Settings"
    modal
    style="max-width: 80vw; min-height: 85vh"
    contentStyle="flex-grow: 1; display: flex; flex-flow: column; gap: 0.5rem; justify-content: space-between"
  >
    <Tabs v-model:value="tab">
      <TabList>
        <Tab
          value="modes"
          :pt="
            app.ui.help.bottom_start(
              `
                <h1>Ionization Modes</h1>

                <p>
                  Define ionization modes, to configure the data processing pipeline to match
                  with the experimental setup.
                </p>
                <p>
                  Ionization mode consists of the applicable ionization mechanisms, mass calibration
                  compounds and default targets used for monitoring instrument performance.
                </p>
                <p>
                  To enable automatic processing of files upon upload, the filename must contain a token
                  matching with an ionization mode configured here.
                </p>
          `,
              { layer, doc: app.ui.help.docUrl('guides/import-files/#set-up-ionization-modes') }
            )
          "
        >
          Ionization Modes
        </Tab>
        <Tab
          value="mechanisms"
          :pt="
            app.ui.help.bottom_start(
              `
                <h1>Ionization Mechanisms</h1>
                <p>
                  Define ionization mechanisms used in ionization modes.
                  The mechanism defines how neutral compounds are ionized to
                  form ions detected by the mass spectrometer.
                </p>
                <p>
                  A mechanism is written in the standard adduct notation: what is
                  added to or removed from the molecule <code>M</code>, inside the
                  brackets, and the charge of the ion after them.
                </p>
                <ul>
                  <li><code>[M+H]+</code> &mdash; protonation (positive)</li>
                  <li><code>[M-H]-</code> &mdash; deprotonation (negative)</li>
                  <li><code>[M+Br]-</code> &mdash; bromide adduct (negative)</li>
                  <li>
                    <code>[M-H]+</code> &mdash; hydride abstraction (positive)
                  </li>
                  <li>
                    <code>[M+CH4N2O+H]+</code> &mdash; a urea cluster, one term
                    per species added (positive)
                  </li>
                  <li>
                    <code>[M]+.</code> or <code>[M]-.</code> &mdash; electron
                    transfer, the dot marking the radical ion
                  </li>
                </ul>
                <p>
                  The older spelling (<code>+H+</code>, <code>-H+</code>,
                  <code>+Br-</code>, a bare <code>+</code>) is still accepted and
                  stored in the standard one. Its trailing sign is the charge of
                  the species moved, not of the ion, so <code>-H+</code> is
                  <code>[M-H]-</code>.
                </p>
          `,
              { layer, doc: app.ui.help.docUrl('concepts/#ionization-modes-and-mechanisms') }
            )
          "
          >Ionization Mechanisms</Tab
        >
      </TabList>
      <TabPanels>
        <TabPanel value="modes">
          <PaneIonizationMode ref="modesPaneRef" />
        </TabPanel>

        <TabPanel value="mechanisms">
          <PaneIonizationMechanism ref="mechanismsPaneRef" />
        </TabPanel>
      </TabPanels>
    </Tabs>

    <menu>
      <Button label="Close" @click="visible = false" />
    </menu>
  </Dialog>
</template>
