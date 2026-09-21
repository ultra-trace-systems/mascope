<script setup>
/**
 * Choose the chemistry of raw files that need one.
 *
 * A file whose name carries no token of a configured ionization mode waits in
 * Raw files as "Needs a chemistry", with no samples. Choosing a mode for each
 * polarity the selected files hold processes them under those modes, as a
 * token would have had them processed (`POST /sample/files/bind`). The server
 * refuses a file that has samples already - re-processing rebuilds those - and
 * processes the rest.
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
const emit = defineEmits(['submit'])

const POLARITY_NAMES = { '-': 'Negative', '+': 'Positive' }

// The polarities the files hold, negative first as everywhere else.
const polarities = computed(() =>
  ['-', '+'].filter((polarity) =>
    props.files.some((file) => (file.polarity ?? '').includes(polarity))
  )
)

const chosen = reactive({ '-': null, '+': null })
watch(visible, (open) => {
  if (open) {
    chosen['-'] = null
    chosen['+'] = null
  }
})

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
    const { message, data } = response.data ?? {}
    app.ui.notification.push({
      type: 'bind_sample_files',
      status: data?.refused?.length ? 'warning' : 'success',
      message
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
      ionization modes you choose, as if their names carried the modes' tokens.
    </p>
    <div class="col" style="gap: 1.5rem; margin: 1.5rem 0 0.5rem">
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
      ionization mode is configured yet. Add one under Edit ionizations.
    </Message>
    <template #footer>
      <Button label="Cancel" severity="secondary" text @click="visible = false" />
      <Button label="Process" :disabled="!ready" :loading="busy" @click="submit" />
    </template>
  </Dialog>
</template>
