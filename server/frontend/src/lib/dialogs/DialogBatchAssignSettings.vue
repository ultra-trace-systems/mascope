<script setup>
import { computed, reactive, ref, watch } from 'vue'

import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import Message from 'primevue/message'

import { getApiErrorMessage } from '@/api/utils'
import { canEditWorkspace } from '@/lib/permissions'
import {
  BATCH_SETTING_KEYS,
  PARAM_KEYS,
  batchSettingsBody,
  batchSettingsModel,
  usePeakAssignParams
} from '@/lib/peakAssignParams'
import { useApp } from '@/stores'

import PeakAssignConfigForm from './PeakAssignConfigForm.vue'

/**
 * Edit assignment settings: a batch's own assignment settings, in a dialog
 * (docs/dev/batch_assignment_one_action.md 3.4).
 *
 * The form is bound to the batch's saved settings (`assignment_settings`,
 * MAS-1014), not to the per-user store: it opens on what the batch saved, and
 * on the defaults for every field it did not. *Save* PATCHes the batch with
 * its settings alone, and starts no pass: the batch's status then says it is
 * behind, *settings changed*, and the next Re-assign runs on them. A viewer
 * sees the same form read-only, with the reason.
 *
 * Opened from the Assign batch menu and from *Change settings* in Details,
 * both in BatchAssignControl.
 */
const props = defineProps({
  // The batch row, as the batch list serves it (with `assignment_settings`).
  batch: {
    type: Object,
    default: null
  }
})
const visible = defineModel('visible', { type: Boolean, default: false })
const emit = defineEmits(['saved'])

const app = useApp()
const store = usePeakAssignParams()

// The run switch is not offered: the batch's pass is the untargeted search.
const hidden = ['run_untargeted']

// Saving the batch needs the editor role on its workspace, as a pass does.
const canEdit = computed(() => canEditWorkspace(app.data.workspace.focused, app.auth.user))
const readonlyReason = 'Ask an editor of this workspace to change these settings.'

// The form's values: the batch's settings over the defaults, seeded on every
// open so what was typed and cancelled last time is gone.
const model = reactive(Object.fromEntries(PARAM_KEYS.map((key) => [key, null])))
// What the batch saved as the dialog opened: Save writes against it.
const opened = ref(null)
const ready = ref(false)
const saving = ref(false)
const error = ref(null)
const form = ref(null)

function seed() {
  opened.value = props.batch?.assignment_settings ?? null
  Object.assign(model, batchSettingsModel(opened.value, store.defaults))
}

watch(
  visible,
  async (open, was) => {
    // The form's help cards live on the launcher dialogs' layer while open;
    // a dialog that was never open leaves another surface's layer alone.
    if (open) app.ui.help.set('dialog_peak_assign')
    else if (was) app.ui.help.set(null)
    if (!open) return
    error.value = null
    ready.value = false
    await store.ensureLoaded()
    seed()
    ready.value = true
  },
  { immediate: true }
)

async function save() {
  if (!canEdit.value || !props.batch) return
  if (form.value && !form.value.commit()) {
    error.value = 'The formula range does not parse. Fix it or clear it to save.'
    return
  }
  const { settings, changed } = batchSettingsBody(
    model,
    opened.value,
    store.defaults,
    BATCH_SETTING_KEYS
  )
  if (!changed) {
    visible.value = false
    return
  }
  saving.value = true
  error.value = null
  try {
    await app.data.batch.saveAssignmentSettings({
      sample_batch_id: props.batch.sample_batch_id,
      assignment_settings: settings
    })
    // The batch row's record event follows; the row is set here as well, so
    // the dialog reopened at once opens on what was saved.
    const row = app.data.batch.list?.find?.(
      (entry) => entry.sample_batch_id === props.batch.sample_batch_id
    )
    if (row) row.assignment_settings = settings
    emit('saved', settings)
    visible.value = false
  } catch (failure) {
    error.value = getApiErrorMessage(failure, 'Could not save the assignment settings.')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <Dialog
    v-model:visible="visible"
    modal
    header="Edit assignment settings"
    class="dialog-batch-assign-settings"
    :style="{ width: '26rem' }"
  >
    <p class="lead">
      Every assignment of this batch runs on these settings, whoever starts it. Saving starts
      nothing: the next <b>Re-assign</b> uses them.
    </p>
    <PeakAssignConfigForm
      v-if="ready"
      ref="form"
      :model="model"
      :hidden="hidden"
      :sample-batch-id="batch?.sample_batch_id ?? null"
      :readonly="!canEdit"
      :readonly-reason="readonlyReason"
    />
    <Message v-if="error" severity="error" size="small" data-testid="settings-error">
      {{ error }}
    </Message>
    <template #footer>
      <template v-if="canEdit">
        <Button label="Cancel" text severity="secondary" @click="visible = false" />
        <Button
          label="Save"
          data-testid="settings-save"
          :loading="saving"
          :disabled="!ready"
          @click="save"
        />
      </template>
      <Button v-else label="Close" text severity="secondary" @click="visible = false" />
    </template>
  </Dialog>
</template>

<style scoped>
.lead {
  margin: 0 0 1.25rem;
  font-size: 0.9rem;
  color: var(--p-text-muted-color);
}
</style>
