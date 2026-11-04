<script setup>
import { ref, onMounted } from 'vue'

import ContextMenu from 'primevue/contextmenu'

import { DialogDatasetOp, DialogDatasetPackage } from '@/lib/dialogs'

import { useDatasetContextMenu } from './stores'

const contextMenu = useDatasetContextMenu()

const contextMenuRef = ref()
onMounted(() => {
  contextMenu.ref = contextMenuRef.value
})
</script>

<template>
  <ContextMenu ref="contextMenuRef" :model="contextMenu.entries" @hide="contextMenu.clear" />
  <DialogDatasetOp v-model:action="contextMenu.dialog.op" :dataset="contextMenu.row" />
  <DialogDatasetPackage v-model:dataset="contextMenu.dialog.publish" />
</template>
