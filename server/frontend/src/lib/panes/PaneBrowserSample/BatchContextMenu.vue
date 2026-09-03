<script setup>
import { ref, onMounted } from 'vue'

import ContextMenu from 'primevue/contextmenu'

import { DialogBatchOp, DialogCalibration } from '@/lib/dialogs'

import { useBatchContextMenu } from './stores'

const contextMenu = useBatchContextMenu()

const contextMenuRef = ref()
onMounted(() => {
  contextMenu.ref = contextMenuRef.value
})
</script>

<template>
  <ContextMenu ref="contextMenuRef" :model="contextMenu.entries" @hide="contextMenu.clear" />
  <DialogBatchOp v-model:action="contextMenu.dialog.op" :batch="contextMenu.row" />
  <DialogCalibration v-model:visible="contextMenu.dialog.calibration" :context="contextMenu.row" />
</template>
