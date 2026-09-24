<script setup>
import { reactive, ref, computed, watch, nextTick } from 'vue'

import Button from 'primevue/button'
import Checkbox from 'primevue/checkbox'
import Select from 'primevue/select'
import MultiSelect from 'primevue/multiselect'
import FloatLabel from 'primevue/floatlabel'
import Dialog from 'primevue/dialog'
import Message from 'primevue/message'

import DialogIonizationOp from './DialogIonizationOp.vue'

import { hasIonizationToken } from '@/lib/ionizationModes'
import { isValidInstrumentName } from '@/lib/utils'
import { useApp } from '@/stores'
import { useInstrument } from '@/stores/data/modules/instrument'
import { TOKENLESS_UPLOADS } from '@/stores/server'

const app = useApp()
// The class of an instrument by name: recorded for its files where the
// server knows it, the name rule otherwise. The same answer the server
// files by, so what this dialog offers is what it will accept.
const instrumentClass = useInstrument().typeOf
// A server that keeps a file without a token, for someone to choose its
// chemistry, needs no token added here.
const server = app.server

const props = defineProps({
  files: {
    type: Array,
    default: () => []
  }
})
const active = defineModel('active')

const emit = defineEmits(['upload'])

const instrument = reactive({
  tof: null,
  orbi: null
})

const ionizationTokens = ref([])
const ionizationDialogVisible = ref(false)

const availableIonizationModes = computed(
  () =>
    app.data.ionization.mode.list
      .map((i) => ({ name: i.ionization_mode_name, token: i.ionization_mode_token }))
      .filter((mode) => mode.token) || []
)

// There must be 1 or 2 ionization modes selected (one per polarity)
const validIonizationSelection = computed(() => {
  return ionizationTokens.value.length >= 1 && ionizationTokens.value.length <= 2
})

const processed = computed(() => {
  const invalid = {
    tof: [],
    orbi: [],
    ionization: []
  }
  const valid = []
  props.files.forEach((file) => {
    // parse filename
    const prefix = file.name.split('_')[0]
    const prefixType = instrumentClass(prefix)
    const ext = file.name.split('.').slice(-1)[0].toLowerCase()
    // check filename validity
    let validInstrumentName = true
    if (ext == 'h5' && prefixType !== 'tof') {
      invalid.tof.push(file)
      validInstrumentName = false
    } else if (ext == 'raw' && prefixType !== 'orbi') {
      invalid.orbi.push(file)
      validInstrumentName = false
    }
    let validIonization = true
    if (
      !server.can(TOKENLESS_UPLOADS) &&
      !hasIonizationToken(file.name, app.data.ionization.mode.list)
    ) {
      invalid.ionization.push(file)
      validIonization = false
    }
    if (validInstrumentName && validIonization) {
      valid.push(file)
    }
  })
  return { invalid, valid }
})

const count = computed(() => ({
  invalid:
    processed.value.invalid.tof.length +
    processed.value.invalid.orbi.length +
    processed.value.invalid.ionization.length,
  valid: processed.value.valid.length,
  total: props.files.length
}))

// The instruments these files will be filed under: one per class that has a
// file needing one. The name is reported with the upload rather than written
// into the file name, so it only has to be a name the server accepts - it
// need not say the instrument's class, and need not be one already known.
const chosenInstruments = computed(() =>
  [
    processed.value.invalid.tof.length > 0 ? instrument.tof : null,
    processed.value.invalid.orbi.length > 0 ? instrument.orbi : null
  ].filter((name) => isValidInstrumentName(name))
)

const newInstruments = computed(() =>
  chosenInstruments.value.filter(
    (name) => !app.data.instrument.list.some((known) => known.instrument === name)
  )
)

// Naming an instrument now creates one, and a typo creates a second
// instrument with its own workspace that nobody asked for. Say which name is
// new and have it confirmed, rather than let a slip pass unremarked.
const confirmedNew = ref(false)
watch(
  () => newInstruments.value.join(','),
  () => (confirmedNew.value = false)
)

const invalid = computed(() => {
  const invalidOrbi =
    processed.value.invalid.orbi.length > 0 && !isValidInstrumentName(instrument.orbi)
  const invalidTof =
    processed.value.invalid.tof.length > 0 && !isValidInstrumentName(instrument.tof)
  const unconfirmedNew = newInstruments.value.length > 0 && !confirmedNew.value
  const invalidIonization =
    processed.value.invalid.ionization.length > 0 && !validIonizationSelection.value
  const noFiles =
    processed.value.invalid.tof.length +
      processed.value.invalid.orbi.length +
      processed.value.invalid.ionization.length +
      processed.value.valid.length ==
    0
  return invalidOrbi || invalidTof || unconfirmedNew || invalidIonization || noFiles
})

// handle file selection
watch(
  () => props.files,
  (files) => {
    if (!files?.length) return
    nextTick(() => {
      if (count.value.total > 0) {
        // Some files need manual handling - show dialog
        active.value = true
      }
    })
  },
  { immediate: true }
)

const upload = () => {
  const allProcessedFiles = new Map() // Use Map to avoid duplicates

  // Create token string from selected tokens
  const tokenString = ionizationTokens.value.join('_')

  // Insert ionization tokens before the file extension, where the name rule
  // reads them.
  const withTokens = (name) => {
    const parts = name.split('.')
    return `${parts.slice(0, -1).join('.')}_${tokenString}.${parts.slice(-1)[0]}`
  }

  // Files whose name names no instrument the server can file them under are
  // uploaded reporting the chosen one, the way a File Agent reports the
  // instrument it watches. The name itself is left alone: the server stores
  // the file under the same `<instrument>_<name>` this dialog used to rename
  // it to, and a reported instrument need not say its class.
  const report = (files, chosen) =>
    files.forEach((file) => {
      const needsIonization = processed.value.invalid.ionization.some(
        (ionFile) => ionFile.name === file.name
      )
      allProcessedFiles.set(file.name, {
        ...file,
        name: needsIonization && tokenString ? withTokens(file.name) : file.name,
        meta: { ...file.meta, instrument: chosen }
      })
    })

  report(processed.value.invalid.tof, instrument.tof)
  report(processed.value.invalid.orbi, instrument.orbi)

  // Process files that only have ionization issues (not already processed above)
  processed.value.invalid.ionization.forEach((file) => {
    // Skip if already processed as TOF or Orbi file
    if (!allProcessedFiles.has(file.name) && tokenString) {
      allProcessedFiles.set(file.name, {
        ...file,
        name: withTokens(file.name)
      })
    }
  })

  // Files refused when they were dropped but fine now - the server's
  // capabilities arrived after the drop, say - go through as they are.
  processed.value.valid.forEach((file) => {
    if (!allProcessedFiles.has(file.name)) allProcessedFiles.set(file.name, file)
  })

  const resolved = Array.from(allProcessedFiles.values())

  active.value = false
  app.uppy.clearInvalid()
  emit('upload', resolved)
}

const cancel = () => {
  active.value = false
  app.uppy.clearInvalid()
}
</script>

<template>
  <Dialog :visible="active" modal header="Resolve issues with uploaded files">
    <!-- TOF FILES -->
    <template v-if="processed.invalid.tof.length > 0">
      <h3>Invalid TOF Files</h3>
      <p>The following h5 files do not have a valid TOF instrument name as a prefix:</p>
      <ul>
        <li v-for="file in processed.invalid.tof" :key="file.name">
          {{ file.name }}
        </li>
      </ul>
      <p>
        <i>
          A file name usually starts with the instrument it belongs to, separated from the rest by
          an underscore. These do not, so pick the instrument they came from below, or type the name
          of a new one. It is reported with the upload; the files keep their own names.
        </i>
      </p>
      <p>Please select or enter an instrument to assign these files to:</p>
      <div class="center" style="width: 100%">
        <FloatLabel>
          <Select
            inputId="file-instrument"
            v-model="instrument.tof"
            :options="
              app.data.instrument.list.filter(
                ({ instrument, type }) => (type ?? instrumentClass(instrument)) == 'tof'
              )
            "
            dataKey="instrument"
            optionLabel="instrument"
            optionValue="instrument"
            editable
            style="min-width: 200px"
          />
          <label for="file-instrument"> Instrument </label>
        </FloatLabel>
        <Message
          severity="warn"
          icon="pi pi-exclamation-triangle"
          v-if="instrument.tof && !isValidInstrumentName(instrument.tof)"
          style="margin-bottom: 2rem"
        >
          <i>
            An instrument name is 1 to 64 letters, digits and hyphens. The underscore separates the
            instrument from the rest of a file name, so it cannot be part of one.
          </i>
        </Message>
      </div>
    </template>
    <!-- ORBI FILES -->
    <template v-if="processed.invalid.orbi.length > 0">
      <h3>Invalid OrbiTrap Files</h3>
      <p>The following raw files do not have a valid Orbitrap instrument name as a prefix:</p>
      <ul>
        <li v-for="file in processed.invalid.orbi" :key="file.name">
          {{ file.name }}
        </li>
      </ul>
      <p>
        <i>
          A file name usually starts with the instrument it belongs to, separated from the rest by
          an underscore. These do not, so pick the instrument they came from below, or type the name
          of a new one. It is reported with the upload; the files keep their own names.
        </i>
      </p>
      <p>Please select or enter an instrument to assign these files to:</p>
      <div class="center" style="width: 100%">
        <FloatLabel>
          <Select
            inputId="file-instrument"
            v-model="instrument.orbi"
            :options="
              app.data.instrument.list.filter(
                ({ instrument, type }) => (type ?? instrumentClass(instrument)) == 'orbi'
              )
            "
            dataKey="instrument"
            optionLabel="instrument"
            optionValue="instrument"
            editable
            style="min-width: 200px"
          />
          <label for="file-instrument"> Instrument </label>
        </FloatLabel>
        <Message
          severity="warn"
          icon="pi pi-exclamation-triangle"
          v-if="instrument.orbi && !isValidInstrumentName(instrument.orbi)"
          style="margin-bottom: 2rem"
        >
          <i>
            An instrument name is 1 to 64 letters, digits and hyphens. The underscore separates the
            instrument from the rest of a file name, so it cannot be part of one.
          </i>
        </Message>
      </div>
    </template>
    <!-- A NAME THE SERVER DOES NOT KNOW MAKES AN INSTRUMENT -->
    <template v-if="newInstruments.length > 0">
      <Message severity="warn" icon="pi pi-exclamation-triangle" :closable="false">
        <span v-if="newInstruments.length === 1">
          <b>{{ newInstruments[0] }}</b> is not an instrument this server has files for. Uploading
          creates it, with its own acquisitions workspace.
        </span>
        <span v-else>
          <b>{{ newInstruments.join(' and ') }}</b> are not instruments this server has files for.
          Uploading creates them, each with its own acquisitions workspace.
        </span>
      </Message>
      <div class="row" style="justify-content: flex-start; gap: 0.5rem; margin: 0.75rem 0">
        <Checkbox v-model="confirmedNew" inputId="confirm-new-instrument" binary />
        <label for="confirm-new-instrument">
          {{ newInstruments.length === 1 ? 'Create it' : 'Create them' }}
        </label>
      </div>
    </template>
    <!-- FINE AS THEY ARE -->
    <template v-if="processed.valid.length > 0">
      <h3>Ready to upload</h3>
      <p>These files can be uploaded as they are:</p>
      <ul>
        <li v-for="file in processed.valid" :key="file.name">
          {{ file.name }}
        </li>
      </ul>
    </template>
    <!-- MISSING IONIZATION -->
    <template v-if="processed.invalid.ionization.length > 0">
      <h3>Files missing ionization mode</h3>
      <p>The following raw files do not have a valid ionization mode token:</p>
      <ul>
        <li v-for="file in processed.invalid.ionization" :key="file.name">
          {{ file.name }}
        </li>
      </ul>
      <p>
        <i>
          Each filename must include a valid ionization mode token in order to be processed
          correctly. For files with scans in both positive and negative polarity, there must be two.
          <br /><br />
          {{
            availableIonizationModes.length > 0
              ? `Currently available tokens: ${availableIonizationModes.map((i) => i.token).join(', ')}`
              : 'Currently no tokens available. Configure tokens in Ionization settings.'
          }}.
        </i>
      </p>
      <p>
        Please either select the applicable ionization modes from existing ones below, or edit the
        ionization modes to create a suitable configuration.
      </p>
      <div class="center" style="width: 100%; gap: 1rem; align-items: flex-start">
        <FloatLabel style="flex-grow: 1">
          <MultiSelect
            inputId="file-ionization"
            v-model="ionizationTokens"
            :options="availableIonizationModes"
            optionLabel="name"
            optionValue="token"
            :maxSelectedLabels="2"
            :selectionLimit="2"
            placeholder="Select 1-2 ionization modes"
          />
          <label for="file-ionization"> Ionization modes </label>
        </FloatLabel>
        <Button
          label="Edit Ionizations"
          icon="pi pi-sliders-h"
          severity="secondary"
          @click="ionizationDialogVisible = true"
          style="margin-top: 1.8rem"
        />
      </div>
      <Message
        severity="warn"
        icon="pi pi-exclamation-triangle"
        v-if="ionizationTokens.length > 0 && !validIonizationSelection"
        style="margin-top: 1rem"
      >
        <i>Please select exactly 1 or 2 ionization modes.</i>
      </Message>
    </template>
    <!-- CONFIRM -->
    <menu style="justify-content: flex-end">
      <Button label="Cancel" icon="pi pi-times" severity="secondary" @click="cancel" />
      <Button label="Save" icon="pi pi-save" :disabled="invalid" @click="upload" />
    </menu>

    <DialogIonizationOp v-model:visible="ionizationDialogVisible" />
  </Dialog>
</template>

<style scoped>
menu {
  display: flex;
  flex-flow: row nowrap;
  gap: 0.5rem;
  justify-content: space-between;
  align-items: center;
  padding: 0;
  margin: 0;
}

menu > :deep(*) {
  margin-bottom: 0;
}

menu :deep(*) {
  font-size: 12px;
}

p,
:deep(.p-message-text) {
  max-width: 450px;
}

.center {
  display: flex;
  justify-content: center;
  align-items: flex-end;
}
</style>
