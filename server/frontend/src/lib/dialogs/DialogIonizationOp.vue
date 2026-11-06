<script setup>
/**
 *  The Chemistry settings: the profiles Mascope ships, and Advanced.
 *
 *  Profiles come first, for a regular user: each says it is shipped and
 *  what it has routed. Advanced holds the rest - the server's own ionization
 *  modes with their filename tokens, the collections a profile is pointed
 *  at instead of its own, and the ionization mechanisms - in tabs of its
 *  own. Storage keeps its names: a profile is an ionization mode Mascope
 *  ships.
 *
 *  `start` says which tab an open lands on: Profiles, or Advanced for an
 *  owner that sends the user here to set up a mode of their own.
 */
import { watch, ref } from 'vue'

import Dialog from 'primevue/dialog'
import Button from 'primevue/button'
import Tabs from 'primevue/tabs'
import TabList from 'primevue/tablist'
import Tab from 'primevue/tab'
import TabPanels from 'primevue/tabpanels'
import TabPanel from 'primevue/tabpanel'

import PaneChemistryProfiles from '@/lib/panes/PaneChemistryProfiles.vue'
import PaneIonizationMechanism from '@/lib/panes/PaneIonizationMechanism.vue'
import PaneIonizationMode from '@/lib/panes/PaneIonizationMode.vue'

import { useApp } from '@/stores'

const app = useApp()

const visible = defineModel('visible')
const props = defineProps({
  start: {
    type: String,
    default: 'profiles',
    validator: (value) => ['profiles', 'advanced'].includes(value)
  }
})
const tab = ref(props.start)
const advancedTab = ref('modes')
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
  if (visible.value) {
    tab.value = props.start
    advancedTab.value = 'modes'
    app.ui.help.set(layer)
    // What each mode has routed moves with every file processed, which no
    // event announces: read it again for this open.
    app.data.ionization.mode.load('chemistry settings')
  } else {
    app.ui.help.set(null)
  }
})
</script>

<template>
  <Dialog
    v-model:visible="visible"
    header="Chemistry"
    modal
    style="max-width: 80vw; height: 85vh"
    contentStyle="flex-grow: 1; display: flex; flex-flow: column; gap: 0.5rem; justify-content: space-between"
  >
    <Tabs v-model:value="tab" class="fill">
      <TabList>
        <Tab
          value="profiles"
          :pt="
            app.ui.help.bottom_start(
              `
                <h1>Profiles</h1>
                <p>
                  The chemistries Mascope ships, on every server from its first start. Each
                  brings its mechanisms and, where it has a reagent of its own, the calibrants
                  its files are calibrated on.
                </p>
                <p>
                  To process a file under one, right-click it in Raw files and choose
                  Choose chemistry. Each row says how many files it has routed, and when the
                  newest was acquired.
                </p>
          `,
              { layer, doc: app.ui.help.docUrl('guides/import-files/#chemistry-profiles') }
            )
          "
        >
          Profiles
        </Tab>
        <Tab
          value="advanced"
          :pt="
            app.ui.help.bottom_start(
              `
                <h1>Advanced</h1>
                <p>
                  This server's own ionization modes, the filename tokens that bind files to
                  them, the ionization mechanisms they are built of, and the collections a
                  profile is pointed at instead of the ones it ships.
                </p>
          `,
              { layer, doc: app.ui.help.docUrl('guides/import-files/#advanced-modes-of-your-own') }
            )
          "
        >
          Advanced
        </Tab>
      </TabList>
      <TabPanels class="fill panels">
        <TabPanel value="profiles" class="fill">
          <PaneChemistryProfiles />
        </TabPanel>

        <TabPanel value="advanced" class="fill">
          <Tabs v-model:value="advancedTab" class="fill">
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
                        A file whose name carries a mode's token is processed under it on upload.
                        The profiles are listed here too, so that a profile can be pointed at a
                        collection of this server's own.
                      </p>
                `,
                    {
                      layer,
                      doc: app.ui.help.docUrl('guides/import-files/#advanced-modes-of-your-own')
                    }
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
                          per species added, saved in alphabetical order (positive)
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
            <TabPanels class="fill panels">
              <TabPanel value="modes" class="fill">
                <PaneIonizationMode ref="modesPaneRef" />
              </TabPanel>

              <TabPanel value="mechanisms" class="fill">
                <PaneIonizationMechanism ref="mechanismsPaneRef" />
              </TabPanel>
            </TabPanels>
          </Tabs>
        </TabPanel>
      </TabPanels>
    </Tabs>

    <menu>
      <Button label="Close" @click="visible = false" />
    </menu>
  </Dialog>
</template>

<style scoped>
/* The dialog is a fixed height; the tabs take what the Close menu leaves, and
   each pane fills its panel, its table scrolling within it. The panel scrolls
   only should the content around a table not leave it room. */
.fill,
.panels > :deep(.fill) {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-flow: column;
}

/* A tab panel is not given this component's scope, hence the :deep above */
.panels {
  overflow-y: auto;
}
</style>
