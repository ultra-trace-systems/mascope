<script setup>
import { computed, ref, watch } from 'vue'

import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import Message from 'primevue/message'
import ProgressSpinner from 'primevue/progressspinner'
import Tag from 'primevue/tag'

import { getApiErrorMessage, isRefusedRequest } from '@/api/utils'
import { useApp } from '@/stores'

// Launcher for copying a curated sample's assignments to the rest of its batch.
//
// The confirmation matters more here than for most actions, because what the
// copy publishes depends on samples the user is not looking at: it lists the
// batch's other samples with the verdict the backend just computed, so the
// eligibility is the server's answer rather than a guess this pane assembles
// from records it happens to have loaded (a sample's run status is not on any
// record the client holds). The list it shows is the partition the confirm
// executes. Design: docs/dev/peak_assignment_copy.md section 7.

const visible = defineModel('visible', { type: Boolean, default: false })

const props = defineProps({
  sample: {
    type: Object,
    default: null
  }
})

const app = useApp()
const loading = ref(false)
const submitting = ref(false)
const preview = ref(null)
const previewError = ref(null)

const sampleName = computed(() => props.sample?.sample_item_name ?? 'this sample')
const destinations = computed(() => preview.value?.destinations ?? [])
const eligible = computed(() => destinations.value.filter((entry) => entry.eligible))
// A copy needs a completed run to copy AND somewhere to copy it to; the launch
// endpoint refuses either way, and the button says so before it is pressed.
const hasSourceRun = computed(() => Boolean(preview.value?.source_peak_assignment_run_id))
const canCopy = computed(() => hasSourceRun.value && eligible.value.length > 0)

async function loadPreview() {
  if (!props.sample?.sample_item_id) return
  loading.value = true
  preview.value = null
  previewError.value = null
  try {
    const data = await app.data.peakAssignmentRun.copyPreview(props.sample.sample_item_id)
    preview.value = data?.[0] ?? null
  } catch (error) {
    previewError.value = getApiErrorMessage(error, 'Could not read the batch for this sample.')
  } finally {
    loading.value = false
  }
}

// Re-read on every open: run states and batch membership move underneath a
// dialog that was opened once and left, and a stale eligibility list is exactly
// the thing this dialog exists to avoid showing.
watch(visible, (open) => {
  if (open) loadPreview()
  else preview.value = null
  app.ui.help.set(open ? 'dialog_copy_assignments' : null)
})

async function launch() {
  if (!props.sample?.sample_item_id) return
  submitting.value = true
  try {
    await app.data.peakAssignmentRun.copyToBatch(props.sample.sample_item_id)
  } catch (error) {
    // The endpoint decides before it answers, so a refusal here is an answer
    // the user can act on ("assign its peaks first"), not a crash. Reported
    // where the user asked, since `errors: 'inline'` holds the generic toast.
    app.ui.notification.push({
      type: 'copy_assignments_to_batch',
      status: isRefusedRequest(error) ? 'warning' : 'error',
      message: getApiErrorMessage(error, 'Could not start the assignment copy.')
    })
  } finally {
    submitting.value = false
    visible.value = false
  }
}
</script>

<template>
  <Dialog
    v-model:visible="visible"
    modal
    :header="`Copy assignments from '${sampleName}'`"
    :style="{ width: '32rem' }"
  >
    <div class="col" style="gap: 1rem; align-items: stretch">
      <Message
        severity="warn"
        :closable="false"
        :pt="
          app.ui.help.right(
            `
            <h1>Copy Assignments</h1>
            <p>
            Takes this sample's latest completed assignment run and publishes it
            onto the batch's other samples, one new run each, in the background.
            </p>
            <p>
            The formulas, roles and alternatives are copied as they stand, so
            any curation on this sample travels with them. The evidence is not:
            each destination's fit score, mass error and confidence tier are
            re-measured against that sample's own peaks, so a sample whose data
            supports a formula less will show a lower tier for it.
            </p>
            <p>
            Verification verdicts are never copied - a verdict is a judgement
            about one sample's evidence.
            </p>`,
            {
              layer: 'dialog_copy_assignments',
              doc: app.ui.help.docUrl('how-it-works/peak-assignment/')
            }
          )
        "
      >
        Copies the formulas from this sample's latest completed run, then re-scores them against
        each destination's own peaks. Verdicts are not copied, and existing runs are kept.
      </Message>

      <div v-if="loading" class="row" style="gap: 0.5rem; align-items: center">
        <ProgressSpinner style="width: 1.5rem; height: 1.5rem" strokeWidth="6" />
        <span>Reading the batch...</span>
      </div>

      <Message v-else-if="previewError" severity="error" :closable="false">
        {{ previewError }}
      </Message>

      <template v-else-if="preview">
        <Message v-if="!hasSourceRun" severity="warn" :closable="false">
          This sample has no completed assignment run to copy. Assign its peaks first.
        </Message>

        <div v-if="destinations.length" class="col" style="gap: 0.25rem; align-items: stretch">
          <span class="text-secondary">
            {{ eligible.length }} of {{ destinations.length }} other sample{{
              destinations.length === 1 ? '' : 's'
            }}
            in this batch will receive a copy:
          </span>
          <ul class="destinations">
            <li v-for="entry in destinations" :key="entry.sample_item_id">
              <span class="name">{{ entry.sample_item_name }}</span>
              <Tag
                :value="entry.eligible ? 'will copy' : entry.reason"
                :severity="entry.eligible ? 'success' : 'secondary'"
              />
            </li>
          </ul>
        </div>
        <Message v-else severity="warn" :closable="false">
          This sample's batch has no other samples to copy to.
        </Message>

        <Message
          v-if="hasSourceRun && destinations.length && !eligible.length"
          severity="warn"
          :closable="false"
        >
          None of the batch's other samples is eligible right now.
        </Message>
      </template>
    </div>
    <template #footer>
      <Button label="Cancel" text severity="secondary" @click="visible = false" />
      <Button
        :label="
          canCopy ? `Copy to ${eligible.length} sample${eligible.length === 1 ? '' : 's'}` : 'Copy'
        "
        icon="pi pi-copy"
        :loading="submitting"
        :disabled="!canCopy || loading"
        @click="launch"
      />
    </template>
  </Dialog>
</template>

<style scoped>
.destinations {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  max-height: 14rem;
  overflow-y: auto;
}

.destinations li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
}

.destinations .name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
