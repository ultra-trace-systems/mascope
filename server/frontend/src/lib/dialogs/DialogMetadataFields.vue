<script setup>
/**
 * The metadata field registry, opened from Settings: the pane in a dialog.
 * Reads the registry each time it opens, so a field another editor added
 * since shows.
 */
import { ref, watch } from 'vue'

import Dialog from 'primevue/dialog'
import Button from 'primevue/button'

import PaneMetadataFields from '@/lib/panes/PaneMetadataFields.vue'
import { useMetadataField } from '@/stores/data/modules/metadataField'

const visible = defineModel('visible')
const registry = useMetadataField()
const pane = ref(null)

watch(
  visible,
  (shown) => {
    if (shown) registry.load('dialog')
    else pane.value?.resetFields()
  },
  { immediate: true }
)
</script>

<template>
  <Dialog
    v-model:visible="visible"
    header="Metadata fields"
    modal
    style="width: min(72rem, 95vw); height: 85vh"
    contentStyle="flex-grow: 1; display: flex; flex-flow: column; gap: 0.5rem; min-height: 0"
  >
    <PaneMetadataFields ref="pane" />
    <menu class="close">
      <Button label="Close" @click="visible = false" />
    </menu>
  </Dialog>
</template>

<style scoped>
.close {
  display: flex;
  justify-content: flex-end;
  padding: 0;
  margin: 0;
}
</style>
