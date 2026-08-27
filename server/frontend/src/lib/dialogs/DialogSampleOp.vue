<script setup>
import { ref, computed, watch, watchEffect, reactive } from 'vue'

import FloatLabel from 'primevue/floatlabel'
import Select from 'primevue/select'
import ScrollPanel from 'primevue/scrollpanel'
import InputText from 'primevue/inputtext'
import Button from 'primevue/button'
import Panel from 'primevue/panel'
import Dialog from 'primevue/dialog'
import Message from 'primevue/message'

import { ToolbarTemplate } from '@/lib/toolbars'
import { clone, strToSnakeCase, beautifySnakeCase, beautifyConstant, genId } from '@/lib/utils'
import { useApp } from '@/stores'
import { ionizationModeChoices } from '@/lib/ionizationModes'
import {
  sampleTypesFilterIdRequired,
  sampleTypesFilterIdOptional,
  sampleTypesFilterIdNotAllowed
} from '@/lib/constants'

const app = useApp()
const layer = 'dialog_sample_op' // Help-mode layer for dialog

const props = defineProps({
  item: {
    type: Object
  }
})

const emit = defineEmits(['submit'])

const original = computed(() => props.item)

// dialog visibility reactivity
const action = defineModel('action') // create, update
const visible = ref(false)
watch(action, (value) => {
  visible.value = !!value
})
watch(visible, (value) => {
  if (!value) {
    action.value = null
    app.ui.help.set(null)
  } else {
    app.ui.help.set(layer)
  }
})

const defaultTemplate = computed(() => ({
  name: 'default',
  attribute_template_id: 'default',
  type: 'sample_item',
  template: [
    // name is always required
    {
      label: 'sample_item_name',
      value: original.value?.sample_item_name,
      required: true,
      placeholder: 'Sample title'
    },
    // and any attributes from the item
    ...Object.entries(original.value?.sample_item_attributes ?? {}).map(([label, value]) => ({
      label,
      value
    }))
  ]
}))

const template = reactive({
  selected: defaultTemplate.value
})
const input = reactive({
  fields: template.selected.template,
  filename: null, // for display
  sample_file_id: null, // the actual reference api uses
  filterId: null,
  instrument: null,
  type: null,
  polarity: null,
  ionization_mode_id: null
})
const initial = ref()
const changedInput = computed(() => JSON.stringify(input) !== initial.value)

const generated = reactive({
  filterId: null
})

const title = computed(
  () =>
    ({
      create: `Create a new sample item`,
      update: `Update sample item "${original.value?.sample_item_name}"`
    })[action.value]
)

// component initialization logic
watch(visible, init)
async function init(active) {
  if (!active) return
  // reset state
  template.selected = defaultTemplate.value
  // reset inputs
  // User is creating or updating a sample item
  input.filename = original.value?.filename
  input.sample_file_id = original.value?.sample_file_id
  input.instrument = original.value?.instrument
  input.polarity = original.value?.polarity ?? null
  if (polarityOptions.value.length === 1) {
    input.polarity = polarityOptions.value[0].value
  }
  input.filterId = original.value?.filter_id ?? null
  input.type = original.value?.sample_item_type ?? 'ONLINE'
  input.ionization_mode_id = original.value?.ionization_mode_id ?? null

  // reset generated
  generated.filterId = null
  // fill fields
  input.fields = Object.entries({
    sample_item_name: original.value?.sample_item_name,
    ...original.value?.sample_item_attributes
  }).map(([label, value]) => ({
    label,
    value
  }))

  initial.value = JSON.stringify(input)
}
// autofill fields when template is selected
watch(template, autofill)
function autofill() {
  const loaded = template.selected
  if (loaded) {
    input.fields = loaded.template.map((newField) => ({
      ...newField,
      value: input.fields.find((oldField) => oldField.label === newField.label)?.value
    }))
  }
}

const filters = computed(() => {
  return app.data.batch.focused
    ? [
        null,
        ...(generated.filterId ? [generated.filterId] : []),
        ...new Set(app.data.sample.list.map(({ filter_id }) => filter_id).filter((f) => f))
      ]
    : [generated.filterId]
})

// Determine sample item type options based on filterId and type constraints
const sampleTypeOptions = computed(() => {
  if (input.filterId) {
    return sampleTypesFilterIdRequired.concat(sampleTypesFilterIdOptional).map((type) => ({
      label: beautifyConstant(type),
      value: type
    }))
  } else {
    return sampleTypesFilterIdOptional.concat(sampleTypesFilterIdNotAllowed).map((type) => ({
      label: beautifyConstant(type),
      value: type
    }))
  }
})

// A mixed-polarity file arrives as '+-' and stays there until the user picks a
// side; nothing downstream - ionization mode included - can be resolved before
// that, since the two polarities are separate configurations.
const polaritySelected = computed(() => ['+', '-'].includes(input.polarity))

// --- Ionization mode
//
// Create: offer every mode in the sample's polarity and preselect the one whose
// token the filename carries. Filenames do not always carry a token the
// configuration knows, and then the user picks the mode by hand - an empty
// dropdown would leave the file unprocessable.
// Update: the mode is fixed (changing it would invalidate the sample's
// calibration and matches), so only the assigned one is listed, disabled.
const ionization = computed(() => {
  if (action.value === 'create') {
    return ionizationModeChoices({
      modes: app.data.ionization.mode.list,
      filename: input.filename,
      polarity: input.polarity
    })
  }
  const assigned = app.data.ionization.mode.list.find(
    (mode) => mode.ionization_mode_id === original.value?.ionization_mode_id
  )
  return {
    options: assigned
      ? [{ label: assigned.ionization_mode_name, value: assigned.ionization_mode_id }]
      : [],
    defaultId: assigned?.ionization_mode_id ?? null
  }
})
const ionizationModeOptions = computed(() => ionization.value.options)

// Apply the preselection, and drop a selection that no longer fits: a
// mixed-polarity file offers a different set of modes per polarity, so
// switching polarity has to re-resolve rather than keep the stale mode.
// Update only ever renders the mode the sample already has - leaving `input`
// alone there keeps a still-loading mode list from nulling it out on save.
watch(
  ionization,
  ({ options, defaultId }) => {
    if (action.value !== 'create') return
    if (!options.some(({ value }) => value === input.ionization_mode_id)) {
      input.ionization_mode_id = defaultId
    }
  },
  { immediate: true }
)

// Why the dropdown is empty, or why it opened without a selection. Doubles as
// the placeholder, so it disappears the moment the user picks a mode.
const ionizationHint = computed(() => {
  if (action.value !== 'create') return null
  if (input.polarity === '+-') return 'Select the polarity first'
  if (!polaritySelected.value) return 'This file has no known polarity'
  if (!ionizationModeOptions.value.length) {
    return 'No ionization mode configured for this polarity'
  }
  if (!ionization.value.defaultId) return 'No mode token in the filename - select one'
  return null
})

// The placeholder has no room for the reason; the tooltip carries it, and both
// go quiet once a mode is on the field.
const ionizationTooltip = computed(() => {
  if (!ionizationHint.value || input.ionization_mode_id) return null
  if (input.polarity === '+-') {
    return (
      'This file holds scans of both polarities, which are configured ' +
      'separately. Pick a polarity to see the ionization modes it can be ' +
      'processed in.'
    )
  }
  if (!polaritySelected.value) {
    return (
      'This file records no polarity, so there is no set of ionization modes ' +
      'to choose from. It cannot be processed as it stands.'
    )
  }
  if (!ionizationModeOptions.value.length) {
    const polarity = input.polarity === '+' ? 'positive' : 'negative'
    return (
      `No ionization mode is configured for ${polarity} polarity. Add one in ` +
      'the ionization settings before processing this file.'
    )
  }
  return (
    'The filename carries no configured ionization mode token, so none could ' +
    'be resolved for it. Select the mode this file was acquired in.'
  )
})

async function save() {
  visible.value = null
  emit('submit')
  const sample_item = {
    sample_batch_id: app.data.batch.focused.sample_batch_id,
    sample_file_id: input.sample_file_id,
    sample_item_name: input.fields.find((field) => field.label == 'sample_item_name').value,
    sample_item_type: input.type,
    filter_id: input.filterId,
    polarity: input.polarity,
    ionization_mode_id: input.ionization_mode_id,
    sample_item_attributes: clone(
      input.fields
        .filter((field) => field.label != 'sample_item_name')
        .reduce(
          (fields, field) => ({
            ...fields,
            [strToSnakeCase(field.label)]: field.value ?? ''
          }),
          {}
        ) ?? {}
    )
  }
  if (props.action == 'create') {
    await app.data.sample.process(sample_item)
  } else if (props.action == 'update') {
    await app.data.sample.update({
      sample: {
        ...props.item, // To include sample_item_id
        ...sample_item,
        filename: input.filename
      }
    })
  }
}

// reset item type when filter ID was changed
watchEffect(() => {
  if (input.filterId !== original.value?.filter_id) {
    input.type = null
  }
})
watchEffect(() => {
  if (sampleTypesFilterIdNotAllowed.includes(input.type)) {
    input.filterId = null
  }
})

const invalid = computed(() => {
  const missingRequiredFields =
    input.fields?.filter((f) => f?.required).length !=
    input.fields?.filter((f) => f?.required).filter((f) => f.value).length
  const invalidUpdate = action.value === 'update' && !changedInput.value // * see note below
  // A sample created without a mode gets neither calibration targets nor
  // ionization mechanisms, so it would match against nothing.
  const missingIonizationMode = action.value === 'create' && !input.ionization_mode_id
  return (
    !input.type ||
    !polaritySelected.value ||
    missingIonizationMode ||
    missingRequiredFields ||
    invalidUpdate
  )
})

const invalidMessage = computed(() => {
  if (!invalid.value || action.value === 'update') return ''
  // "Fill in the fields" is a dead end when the blocker is the ionization mode:
  // the fix is a polarity choice, or the ionization settings - not this form.
  if (!input.ionization_mode_id && ionizationHint.value) {
    return ionizationHint.value
  }
  return 'Please fill in all required fields'
})

const polarityOptions = computed(() => {
  const from = original?.value ?? input
  switch (from.polarity) {
    case '+':
      return [{ label: 'Positive', value: '+' }]
    case '-':
      return [{ label: 'Negative', value: '-' }]
    case '+-':
      return [
        { label: 'Positive', value: '+' },
        { label: 'Negative', value: '-' }
      ]
    default:
      return [{ label: 'Unknown', value: null }]
  }
})
</script>

<template>
  <Dialog
    :header="title"
    v-model:visible="visible"
    style="width: 900px"
    contentStyle="flex-grow: 1; display: flex; flex-flow: column; gap: 0.5rem; justify-content: space-between"
  >
    <Panel>
      <ScrollPanel style="width: 100%; height: 50vh">
        <div class="sample-field-grid">
          <FloatLabel v-for="field in input.fields" :key="field.label">
            <InputText :id="`field-${field.label}`" v-model="field.value" required />
            <label :for="`field-${field.label}`">
              {{ beautifySnakeCase(field.label) }}
            </label>
          </FloatLabel>

          <div class="input-group">
            <FloatLabel>
              <Select
                inputId="item-filter-id"
                v-model="input.filterId"
                :options="filters"
                :disabled="sampleTypesFilterIdNotAllowed.includes(input.type)"
                style="min-width: 200px"
              />
              <label for="item-filter-id">Filter ID</label>
            </FloatLabel>
            <Button
              @click="input.filterId = generated.filterId = genId(6, false)"
              icon="pi pi-sparkles"
              :disabled="sampleTypesFilterIdNotAllowed.includes(input.type)"
              v-tooltip="'Generate new filter ID'"
            />
          </div>

          <FloatLabel>
            <Select
              inputId="item-type"
              v-model="input.type"
              :options="sampleTypeOptions"
              dataKey="value"
              optionValue="value"
              optionLabel="label"
            />
            <label for="item-type"> Sample type </label>
          </FloatLabel>

          <FloatLabel>
            <Select
              inputId="polarity-type"
              v-model="input.polarity"
              :options="polarityOptions"
              dataKey="value"
              optionValue="value"
              optionLabel="label"
              :disabled="polarityOptions.length === 1"
            />
            <label for="item-type"> Polarity </label>
          </FloatLabel>

          <FloatLabel>
            <InputText id="item-filename" v-model="input.filename" required disabled />
            <label for="item-filename"> Filename </label>
          </FloatLabel>

          <FloatLabel>
            <Select
              inputId="ionization-mode"
              v-model="input.ionization_mode_id"
              :options="ionizationModeOptions"
              dataKey="value"
              optionValue="value"
              optionLabel="label"
              :disabled="action !== 'create'"
              :placeholder="ionizationHint ?? 'Select ionization mode'"
              v-tooltip.top="ionizationTooltip"
            />
            <label for="ionization-mode">Ionization Mode</label>
          </FloatLabel>
        </div>
      </ScrollPanel>
    </Panel>
    <menu>
      <ToolbarTemplate v-model:template="template.selected" :default="defaultTemplate" />
      <menu>
        <Message v-if="invalidMessage" severity="error">
          {{ invalidMessage }}
        </Message>
        <Button label="Cancel" @click="() => (action = null)" severity="secondary" />
        <Button label="Save" @click="() => save()" :disabled="invalid" />
      </menu>
    </menu>
  </Dialog>
</template>

<style scoped>
.sample-field-grid {
  margin: 0rem auto;
  width: 90%;
  min-height: 100%;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(30ch, 100%), 1fr));
  grid-auto-rows: auto;
  align-items: baseline;
  justify-items: stretch;
  justify-content: center;
  align-content: center;
  gap: 2rem;
}

.input-group {
  padding: 0;
  margin: 0;
  gap: 0.5rem;
  display: flex;
  flex-flow: row nowrap;
  align-items: baseline;
  width: 100%;
  max-width: 100%;
  min-width: 0;
}

.input-group :deep(*) {
  margin: 0;
}

:deep(.p-select),
:deep(.p-inputtext) {
  min-width: 0;
  width: 100%;
  max-width: 100%;
}

/* Override the global min-width for grid items */
.sample-field-grid > * {
  min-width: 0;
  max-width: 100%;
}

menu {
  margin-top: 1rem;
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
}
</style>
