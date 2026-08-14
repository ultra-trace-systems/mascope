<script setup>
import { computed, reactive, ref, watch } from 'vue'

import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import Message from 'primevue/message'

import { useApp } from '@/stores'

import PeakAssignConfigForm from './PeakAssignConfigForm.vue'

// Launcher for batch peak assignment.
//
// The batch is the one entry point where every setting is multiplied by the
// number of samples, and until now it was also the only one with no settings at
// all - a single click ran the heaviest configuration over everything. This
// gives the batch the same knobs as the per-sample launcher, but starts from the
// server's batch default (Stage A only) and says plainly what turning the
// untargeted stage on will cost.

const visible = defineModel('visible', { type: Boolean, default: false })

const props = defineProps({
  batch: {
    type: Object,
    default: null
  }
})

const app = useApp()
const submitting = ref(false)

// Mirrors default_batch_config() on the backend: Stage A only. The remaining
// fields are left null so the form fills them from /params.
function initialConfig() {
  return {
    run_untargeted: false,
    mz_precision_ppm: null,
    formula_ranges: null,
    max_untargeted_peaks: null,
    peak_intensity_threshold: null,
    max_alternatives: null
  }
}

const config = reactive(initialConfig())

// Reset each time the dialog opens, so a previous batch's choices never leak
// into the next one.
watch(visible, (open) => {
  if (open) Object.assign(config, initialConfig())
})

const batchName = computed(() => props.batch?.sample_batch_name ?? 'this batch')

async function launch() {
  if (!props.batch?.sample_batch_id) return
  submitting.value = true
  try {
    // Drop anything the form left unset so the backend default applies rather
    // than a null overriding it.
    const payload = Object.fromEntries(
      Object.entries(config).filter(([, value]) => value !== null && value !== '')
    )
    await app.data.batch.assign({
      sample_batch_id: props.batch.sample_batch_id,
      config: payload
    })
    visible.value = false
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <Dialog
    v-model:visible="visible"
    modal
    :header="`Assign peaks for '${batchName}'`"
    :style="{ width: '28rem' }"
  >
    <div class="col" style="gap: 1rem; align-items: stretch">
      <Message severity="warn" :closable="false">
        Runs over every eligible sample in the batch, in the background. Blank and uncalibrated
        samples are skipped. It cannot be cancelled once started.
      </Message>
      <Message v-if="config.run_untargeted" severity="warn" :closable="false">
        The untargeted search runs per sample and is the slowest part of assignment. Across a whole
        batch this can take a long time.
      </Message>
      <PeakAssignConfigForm :config="config" :pinned="['run_untargeted']" />
    </div>
    <template #footer>
      <Button label="Cancel" text severity="secondary" @click="visible = false" />
      <Button
        label="Assign"
        icon="pi ph ph-atom"
        :loading="submitting"
        :disabled="!batch"
        @click="launch"
      />
    </template>
  </Dialog>
</template>
