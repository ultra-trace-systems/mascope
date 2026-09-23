<script setup>
/**
 * Choose the chemistry of raw files.
 *
 * A file whose name carries no token of a configured ionization mode waits in
 * Raw files as "Needs a chemistry", with no samples. Choosing a mode for each
 * polarity the selected files hold processes them under those modes, as a
 * token would have had them processed (`POST /sample/files/bind`); a file
 * that has samples already, bound wrongly, is rebuilt under them, and one a
 * person made a sample from keeps its m/z calibration. The server refuses a
 * file being processed, and answers 207 when it refused some and processed
 * the rest.
 *
 * A file waits here precisely because its name named no mode, so often enough
 * no configured mode is the right one either. `configure` asks the owner to
 * open the ionization settings; it brings the dialog back afterwards.
 */
import { computed, reactive, ref, watch } from 'vue'

import Dialog from 'primevue/dialog'
import Button from 'primevue/button'
import Select from 'primevue/select'
import FloatLabel from 'primevue/floatlabel'
import Message from 'primevue/message'

import { api } from '@/api'
import { useApp } from '@/stores'

const app = useApp()

const visible = defineModel('visible')
const props = defineProps({
  files: {
    type: Array,
    default: () => []
  }
})
const emit = defineEmits(['submit', 'configure'])

const POLARITY_NAMES = { '-': 'Negative', '+': 'Positive' }

// The polarities the files hold, negative first as everywhere else.
const polarities = computed(() =>
  ['-', '+'].filter((polarity) =>
    props.files.some((file) => (file.polarity ?? '').includes(polarity))
  )
)

const options = (polarity) =>
  (app.data.ionization.mode.list ?? [])
    .filter((mode) => mode.ionization_mode_polarity === polarity)
    .sort((a, b) => a.ionization_mode_name.localeCompare(b.ionization_mode_name))
    .map((mode) => ({
      label: mode.ionization_mode_token
        ? `${mode.ionization_mode_name} (${mode.ionization_mode_token})`
        : mode.ionization_mode_name,
      value: mode.ionization_mode_id
    }))

const chosen = reactive({ '-': null, '+': null })

// The dialog gives way to the ionization settings and is reopened afterwards,
// which is an open like any other - but the point of going there was to come
// back and use what was set up, so a mode already picked for the other
// polarity has to survive it. Anything else opens on a clean pair.
const resuming = ref(false)
watch(visible, (open) => {
  if (!open) return
  if (resuming.value) {
    resuming.value = false
    // A mode can be renamed or deleted while the settings are open, so keep
    // only what the options still offer rather than a dangling id.
    for (const polarity of ['-', '+']) {
      const offered = options(polarity).some(({ value }) => value === chosen[polarity])
      if (!offered) chosen[polarity] = null
    }
    return
  }
  chosen['-'] = null
  chosen['+'] = null
})

const configure = () => {
  resuming.value = true
  emit('configure')
}

const missing = computed(() =>
  polarities.value.filter((polarity) => options(polarity).length === 0)
)
const ready = computed(
  () => polarities.value.length > 0 && polarities.value.every((polarity) => chosen[polarity])
)
const busy = ref(false)

async function submit() {
  busy.value = true
  try {
    const response = await api.http.post(
      '/sample/files/bind',
      {
        sample_file_ids: props.files.map(({ sample_file_id }) => sample_file_id),
        ionization_mode_ids: polarities.value.map((polarity) => chosen[polarity])
      },
      { type: 'bind_sample_files' }
    )
    // 207: some were refused, and the body is the warning naming them.
    const partly = response.status === 207
    const { message, error } = response.data ?? {}
    app.ui.notification.push({
      type: 'bind_sample_files',
      status: partly ? 'warning' : 'success',
      message: partly ? error : message
    })
    visible.value = false
    emit('submit')
  } catch {
    // Reported by the HTTP layer; the dialog stays open to try again.
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <Dialog v-model:visible="visible" modal header="Choose chemistry" style="width: 30rem">
    <p>
      {{ files.length }} {{ files.length === 1 ? 'file is' : 'files are' }} processed under the
      ionization modes you choose, as if their names carried the modes' tokens. A file that has
      samples already is rebuilt under them.
    </p>
    <!-- stretch: .col centers its children, which leaves each field as wide as
         its own content - and an empty Select has none, so it collapses. -->
    <div class="col" style="align-items: stretch; gap: 1.5rem; margin: 1.5rem 0 0.5rem">
      <FloatLabel v-for="polarity in polarities" :key="polarity" variant="on">
        <Select
          :inputId="`chemistry-${polarity === '-' ? 'negative' : 'positive'}`"
          v-model="chosen[polarity]"
          :options="options(polarity)"
          optionLabel="label"
          optionValue="value"
          style="width: 100%"
        />
        <label :for="`chemistry-${polarity === '-' ? 'negative' : 'positive'}`">
          {{ POLARITY_NAMES[polarity] }} ionization mode
        </label>
      </FloatLabel>
    </div>
    <Message v-if="missing.length" severity="warn" :closable="false">
      No {{ missing.map((polarity) => POLARITY_NAMES[polarity].toLowerCase()).join(' or ') }}
      ionization mode is configured yet.
    </Message>
    <p class="chemistry-setup">
      Missing the mode these files were run under?
      <Button
        label="Set up ionization modes"
        icon="pi pi-sliders-h"
        link
        size="small"
        @click="configure"
      />
    </p>
    <template #footer>
      <Button label="Cancel" severity="secondary" text @click="visible = false" />
      <Button label="Process" :disabled="!ready" :loading="busy" @click="submit" />
    </template>
  </Dialog>
</template>

<style scoped>
/* The offer sits below the fields as a quieter aside, on one line with its
   button, so that it reads as a way out rather than a second question. */
.chemistry-setup {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.25rem;
  margin-top: 1rem;
  font-size: 0.875rem;
  color: var(--p-text-muted-color);
}
</style>
