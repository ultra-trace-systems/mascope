<script setup>
import { ref, reactive, computed, watch } from 'vue'

import FloatLabel from 'primevue/floatlabel'
import Select from 'primevue/select'
import ScrollPanel from 'primevue/scrollpanel'
import Panel from 'primevue/panel'
import Button from 'primevue/button'
import Tabs from 'primevue/tabs'
import TabList from 'primevue/tablist'
import Tab from 'primevue/tab'
import TabPanels from 'primevue/tabpanels'
import TabPanel from 'primevue/tabpanel'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Message from 'primevue/message'
import Dialog from 'primevue/dialog'
import { useConfirm } from 'primevue/useconfirm'

import { fromSpreadsheet } from '@/lib/table'
import { sampleTypesFilterIdOptional, sampleTypesFilterIdNotAllowed } from '@/lib/constants'
import { useApp } from '@/stores'
import { BaseClipboardContext } from '@/lib/base'
import { genId } from '@/lib/utils'

import { useValidation } from './validation.js'
import { generic } from './generic.js'
import { autosampler } from './autosampler.js'

const app = useApp()
const confirm = useConfirm()

const visible = defineModel('visible')

const props = defineProps({
  files: {
    type: Object,
    required: true
  },
  polarity: {
    type: String,
    default: ''
  }
})

const emit = defineEmits(['submit'])

const tab = ref('data')

const imported = reactive({
  parsed: [],
  items: [],
  type: null,
  filterId: null,
  availableIonizationModes: []
})
const generated = reactive({
  filterId: null
})
const filters = computed(() => {
  return app.data.batch.focused
    ? [
        null,
        ...(generated.filterId ? [generated.filterId] : []),
        ...new Set(app.data.sample.list.map(({ filter_id }) => filter_id).filter((f) => f))
      ]
    : generated
})

const validation = useValidation({ imported })

const allowedSampleTypes = computed(() => {
  return sampleTypesFilterIdOptional.concat(sampleTypesFilterIdNotAllowed).join(', ')
})

const availableIonizationModes = computed(
  () =>
    app.data.ionization.mode.list
      .map((i) => ({ name: i.ionization_mode_name, token: i.ionization_mode_token }))
      .filter((mode) => mode.token) || []
)

const title = computed(() => {
  const name = app.data.batch.focused?.sample_batch_name ?? 'selected'
  return imported.type == 'autosampler'
    ? `Import autosampler sample data to "${name}" batch`
    : `Import spreadsheet sample data to "${name}" batch`
})

const filesPreview = computed(() =>
  [...props.files]
    .sort((a, b) => new Date(a.datetime) - new Date(b.datetime))
    .map((file) => ({
      ...file,
      sample_item_name: '',
      sample_item_type: '',
      filter_id: ''
    }))
)

// Sample Items Tab
const coreColumns = [
  { field: 'datetime', label: 'Datetime' },
  { field: 'filename', label: 'Filename' },
  { field: 'sample_item_name', label: 'Sample Name' },
  { field: 'sample_item_type', label: 'Sample Type' },
  { field: 'filter_id', label: 'Filter ID' }
]
const attributeColumns = computed(() => {
  if (imported.items.length === 0) return []
  return [
    // find unique attributes
    ...new Set(imported.items.map((item) => Object.keys(item.sample_item_attributes)).flat())
  ].map((key) => ({
    field: `sample_item_attributes.${key}`,
    label: key.replace(/_/g, ' ').replace(/\b\w/g, (l) => l.toUpperCase())
  }))
})
const allColumns = computed(() => [...coreColumns, ...attributeColumns.value])

watch(visible, init)
function init(active) {
  if (!active) return
  tab.value = 'data'
  imported.parsed = []
  imported.items = []
  imported.filterId = ''
  imported.type = null
  imported.availableIonizationModes = availableIonizationModes.value
  validation.reset()
}

// processing
async function parse(text) {
  init()
  const { cols, rows } = fromSpreadsheet(text)
  if (!cols.length || !rows.length) return
  // columns
  const autosamplerKeys = ['ht3000a_autorun_report', 'software', 'sample_list', 'autosampler']
  imported.type = cols.some((col) => autosamplerKeys.includes(col.field))
    ? 'autosampler'
    : 'generic'
  validation.cols.execute({ cols })
  if (!validation.cols.passed) return
  // rows
  if (imported.type === 'autosampler') {
    imported.parsed = autosampler.parse(rows)
  } else if (imported.type === 'generic') {
    imported.parsed = generic.parse(cols, rows)
  }
  if (imported.parsed) {
    preprocess()
  }
}

watch(() => imported.filterId, preprocess)
function preprocess() {
  // Sort acquisitions by datetime in descending order
  const acquisitions = [...props.files].sort((a, b) => new Date(a.datetime) - new Date(b.datetime))
  if (imported.type === 'autosampler') {
    if (!imported.filterId) {
      imported.filterId = generated.filterId = genId(6, false)
    }
    imported.items = autosampler.preprocess(acquisitions, imported.parsed, imported.filterId)
  }
  if (imported.type === 'generic') {
    imported.items = generic.preprocess(acquisitions, imported.parsed, props.polarity)
  }
  validation.rows.execute({ files: props.files })
}

watch(
  () => validation.passed,
  (passed) => {
    if (tab.value == 'issues' && passed) {
      // switch to data data
      tab.value = 'data'
    }
  }
)

watch(
  () => validation.issues,
  (issues) => {
    if (issues > 0 && tab.value == 'data') {
      tab.value = 'issues'
    }
  }
)

const submit = () => {
  if (!validation.rows.passed) {
    return
  }
  emit('submit')
  app.data.batch.importSamples({
    batch: app.data.batch.focused,
    sample_items: imported.items
  })
  visible.value = false
}
</script>

<template>
  <Dialog v-model:visible="visible" :header="title">
    <Tabs v-model:value="tab">
      <TabList>
        <Tab value="data">Data</Tab>
        <Tab
          value="issues"
          :disabled="validation.cols.issues.length == 0 && validation.rows.issues.length == 0"
        >
          Issues
        </Tab>
      </TabList>
      <TabPanels>
        <TabPanel
          value="data"
          :pt="
            app.ui.help.top(
              `
              <b>Paste sample metadata from a spreadsheet.</b>
              <ul>
              <li>Required fields are 'name' and 'type'.</li>
              <li>
                You may include 'filter id' if applicable, or any other extra attributes
                each in their own column.
              </li>
              <li>
                Allowed sample types are:<br />
                ${allowedSampleTypes}
              </li>
              </ul>
            `,
              { doc: app.ui.help.docUrl('guides/import-files/#alternative-process-raw-files-by-hand') }
            )
          "
        >
          <BaseClipboardContext :parse="parse" :persistMessage="imported.items.length == 0">
            <template v-slot:info>
              <div id="preview" v-if="imported.items.length == 0">
                <Panel>
                  <ScrollPanel style="height: 25vh; max-width: 80vw">
                    <DataTable
                      dataKey="filename"
                      :value="filesPreview"
                      scrollable
                      scrollHeight="300px"
                      tableStyle="max-width: 70vw"
                    >
                      <Column style="width: 10ch" />
                      <Column
                        v-for="col of coreColumns"
                        :key="col.field"
                        :field="col.field"
                        :header="col.label"
                      />
                    </DataTable>
                  </ScrollPanel>
                  <Message severity="secondary" icon="pi pi-clipboard">
                    <b>Paste sample metadata from a spreadsheet.</b>
                    <br /><br />
                    Activate help mode for detailed information.
                  </Message>
                </Panel>
              </div>
            </template>
            <p v-if="imported.type">
              Please check carefully the details of the samples parsed from the
              {{ imported.type == 'autosampler' ? 'autosample report' : 'spreedsheet input:' }}
            </p>
            <Panel v-if="imported.items.length > 0">
              <ScrollPanel style="height: 25vh; max-width: 80vw">
                <DataTable
                  dataKey="filename"
                  :value="imported.items"
                  scrollable
                  scrollHeight="300px"
                  tableStyle="max-width: 70vw"
                >
                  <Column style="width: 10ch" />
                  <Column
                    v-for="col of allColumns"
                    :key="col.field"
                    :field="col.field"
                    :header="col.label"
                  />
                </DataTable>
              </ScrollPanel>
            </Panel>
          </BaseClipboardContext>
        </TabPanel>
        <TabPanel value="issues">
          <Panel>
            <ScrollPanel style="height: 300px; max-width: 80vw">
              <section v-if="validation.cols.issues.length > 0">
                <b>Column issues:</b>
                <Message
                  icon="pi pi-exclamation-circle"
                  severity="secondary"
                  v-for="msg in validation.cols.issues"
                  :key="msg"
                  :closable="false"
                >
                  {{ msg }}
                </Message>
              </section>
              <section v-if="validation.rows.issues.length > 0">
                <b>Row issues</b>
                <template v-for="issue in validation.rows.issues" :key="issue.key">
                  <p v-if="issue.sample">
                    Item <i>{{ issue.sample }}</i> (#{{ issue.key }}):
                  </p>
                  <Message
                    icon="pi pi-exclamation-circle"
                    v-for="failure in issue.failures"
                    severity="secondary"
                    :closable="false"
                    :key="failure"
                  >
                    {{ failure }}
                  </Message>
                </template>
              </section>
            </ScrollPanel>
          </Panel>
        </TabPanel>
      </TabPanels>
    </Tabs>
    <!-- Dialog Menu -->
    <menu style="justify-content: space-between">
      <div v-if="imported.type == 'general'" />
      <menu v-else>
        <FloatLabel>
          <Select inputId="item-filter-id" v-model="imported.filterId" :options="filters" />
          <label for="item-filter-id">Filter ID</label>
        </FloatLabel>
        <Button
          @click="imported.filterId = generated.filterId = genId(6, false)"
          icon="pi pi-sparkles"
        />
      </menu>
      <menu>
        <Button label="Cancel" severity="secondary" @click="visible = false" />
        <Button
          :label="`Process (${imported.items.length})`"
          :disabled="!validation.passed"
          @click="
            () => {
              confirm.require({
                icon: 'pi pi-info-circle',
                header: 'Import samples',
                message: `Are you sure you want to import ${imported.items.length} samples into the batch '${app.data.batch.focused?.sample_batch_name}'?`,
                accept: submit,
                acceptProps: {
                  icon: 'pi pi-file-import',
                  label: 'Import'
                },
                rejectProps: {
                  icon: 'pi pi-times',
                  label: 'Cancel',
                  severity: 'secondary'
                }
              })
            }
          "
        />
      </menu>
    </menu>
  </Dialog>
</template>

<style scoped>
.item-filter {
  padding: 0;
  margin: 0;
  gap: 0.5rem;
  display: flex;
  flex-flow: row nowrap;
  align-items: baseline;
}
.item-filter :deep(*) {
  margin: 0;
}

:deep(.p-select) {
  min-width: 200px;
}

:deep(.p-message) {
  max-width: 500px;
  margin: 0.5rem auto;
}

#preview {
  position: relative;
}

#preview :deep(.p-message) {
  position: absolute;
  right: 2rem;
  top: 5rem;
  z-index: 10;
}
</style>
