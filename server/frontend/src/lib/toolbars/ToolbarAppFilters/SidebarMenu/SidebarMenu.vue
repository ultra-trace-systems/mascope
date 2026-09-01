<script setup>
import { computed, ref, watchEffect } from 'vue'

import Button from 'primevue/button'
import Drawer from 'primevue/drawer'
import Tabs from 'primevue/tabs'
import TabList from 'primevue/tablist'
import Tab from 'primevue/tab'
import TabPanels from 'primevue/tabpanels'
import TabPanel from 'primevue/tabpanel'
import ContextMenu from 'primevue/contextmenu'

import { DialogWorkspaceOp, DialogWorkspaceMembership, DialogDatasetOp } from '@/lib/dialogs'
import { BatchContextMenu, useBatchContextMenu, useBatchTableConfig } from '@/lib/panes'

import { useSidebarMenu } from './state.js'
import WorkspacePane from './WorkspacePane.vue'
import UserSettingsPane from './UserSettingsPane.vue'
import NotificationPane from './NotificationPane.vue'
import NotificationOverlay from './NotificationOverlay.vue'

import { useApp } from '@/stores'
import { workspaceIcon } from '@/stores/data/modules/workspace'

const app = useApp()
const sidebarMenu = useSidebarMenu()

const dialog = ref()
const datasetDialog = ref()
const workspaceContextMenu = ref()
const workspaceMembersDialog = ref(false)
const datasetContextMenu = ref()
const batchContextMenu = useBatchContextMenu()
const batchTable = useBatchTableConfig()

// Navigation between batches
const batch = computed(() => app.data.batch.focused)

const batchIndex = computed(() => {
  if (!batch.value || batchTable.sortedFilteredBatchList.length === 0) return -1
  return batchTable.sortedFilteredBatchList.findIndex(
    (b) => b.sample_batch_id === batch.value.sample_batch_id
  )
})

const previousBatch = () => {
  if (batchTable.sortedFilteredBatchList.length === 0) return
  const currentIndex = batchIndex.value
  if (currentIndex <= 0) return
  app.data.batch.focused = batchTable.sortedFilteredBatchList[currentIndex - 1]
}

const nextBatch = () => {
  if (batchTable.sortedFilteredBatchList.length === 0) return
  const currentIndex = batchIndex.value
  if (currentIndex >= batchTable.sortedFilteredBatchList.length - 1) return
  app.data.batch.focused = batchTable.sortedFilteredBatchList[currentIndex + 1]
}

watchEffect(() => {
  if (!sidebarMenu.open) {
    sidebarMenu.tab = 'workspaces'
  }
})

// Auto-open sidebar when no workspace is selected
watchEffect(() => {
  if (!app.data.workspace.focused && !sidebarMenu.open) {
    sidebarMenu.open = true
  }
})

// Prevent closing without a workspace selected
const canClose = computed(() => !!app.data.workspace.focused)

// PrimeVue's Drawer closes on any document click outside its DOM tree.
// Dialogs and overlays with appendTo="body" are teleported out of the Drawer,
// so clicking inside them triggers the Drawer's outside-click handler. We block
// the close when any PrimeVue overlay layer is present in the DOM.
const onDrawerVisibleUpdate = (val) => {
  if (
    !val &&
    document.querySelector('.p-dialog-mask, .p-overlay-mask, [data-pc-section="overlay"]')
  )
    return
  sidebarMenu.open = val
}

watchEffect(() => {
  if (sidebarMenu.open && sidebarMenu.tab === 'notifications') {
    app.ui.notification.clearRecentBadge()
  }
})
watchEffect(() => {
  if (!sidebarMenu.open) {
    app.ui.help.set(null)
  }
})
</script>

<template>
  <menu class="breadcrum">
    <NotificationOverlay>
      <Button
        v-tooltip.bottom="'Home menu'"
        icon="pi ph ph-house"
        severity="secondary"
        text
        @click="
          (event) => {
            sidebarMenu.open = true
          }
        "
      />
    </NotificationOverlay>
    <span class="pi ph ph-caret-right" style="opacity: 0.5" />
    <Button
      :icon="`pi ph ${workspaceIcon(app.data.workspace.focused)}`"
      :label="app.data.workspace.focused?.workspace_name"
      v-tooltip.bottom="
        `${app.data.workspace.focused?.workspace_description ?? 'No description'}
                         (right click for options)`
      "
      severity="secondary"
      text
      @click="
        () => {
          app.data.dataset.unfocus()
        }
      "
      @contextmenu="
        (event) => {
          event.preventDefault()
          workspaceContextMenu.toggle(event)
        }
      "
    />
    <template v-if="app.data.dataset.focused">
      <span class="pi ph ph-caret-right" style="opacity: 0.5" />
      <Button
        icon="pi ph ph-folder-open"
        :label="app.data.dataset.focused?.dataset_name"
        v-tooltip.bottom="
          `${app.data.dataset.focused?.dataset_description ?? 'No description'}
                         (right click for options)`
        "
        severity="secondary"
        text
        @click="
          () => {
            app.data.batch.unfocus()
          }
        "
        @contextmenu="
          (event) => {
            event.preventDefault()
            datasetContextMenu.toggle(event)
          }
        "
      />
    </template>
    <template v-if="app.data.batch.focused">
      <span class="pi ph ph-caret-right" style="opacity: 0.5" />
      <Button
        v-tooltip.bottom="
          batchIndex > 0
            ? 'Previous batch: ' +
              (batchTable.sortedFilteredBatchList[batchIndex - 1]?.sample_batch_name ?? '')
            : undefined
        "
        text
        icon="pi pi-arrow-left"
        @click="previousBatch"
        :disabled="batchIndex <= 0"
      />
      <Button
        icon="pi pi-hashtag"
        :label="batch.sample_batch_name"
        v-tooltip.bottom="
          `${batch?.sample_batch_description ?? 'No description'}
                          (right click for options)`
        "
        severity="secondary"
        text
        @click="
          () => {
            app.data.sample.unfocus()
          }
        "
        @contextmenu="
          (event) => {
            event.preventDefault()
            batchContextMenu.onClick(event)
          }
        "
      />
      <Button
        v-tooltip.bottom="
          batchIndex < batchTable.sortedFilteredBatchList.length - 1
            ? 'Next batch: ' +
              (batchTable.sortedFilteredBatchList[batchIndex + 1]?.sample_batch_name ?? '')
            : undefined
        "
        text
        icon="pi pi-arrow-right"
        @click="nextBatch"
        :disabled="batchIndex >= batchTable.filteredBatchList.length - 1"
      />
    </template>
  </menu>
  <Tabs v-model:value="sidebarMenu.tab">
    <Drawer
      :visible="sidebarMenu.open"
      @update:visible="onDrawerVisibleUpdate"
      header="Mascope"
      position="left"
      :style="`width: calc(${app.ui.split.left}vw + 1rem);`"
      :modal="!canClose"
      :dismissable="canClose"
      :closable="canClose"
    >
      <template #header>
        <TabList>
          <Tab value="workspaces" v-tooltip.bottom="'Workspaces'">
            <span class="pi ph ph-briefcase" />
          </Tab>
          <Tab value="notifications" v-tooltip.bottom="'Notifications'">
            <NotificationOverlay>
              <span class="pi ph ph-bell" />
            </NotificationOverlay>
          </Tab>
          <Tab value="settings" v-tooltip.bottom="'Settings'">
            <span class="pi ph ph-gear-six" />
          </Tab>
        </TabList>
      </template>
      <TabPanels>
        <TabPanel value="workspaces">
          <WorkspacePane />
        </TabPanel>
        <TabPanel value="notifications">
          <NotificationPane />
        </TabPanel>
        <TabPanel value="settings">
          <UserSettingsPane />
        </TabPanel>
      </TabPanels>
      <template #footer>
        <div class="row">
          <span class="user-info">Logged in as {{ app.auth.user.username }}</span>
          <Button
            icon="pi pi-sign-out"
            label="Logout"
            @click="app.auth.logout"
            style="margin-top: 1rem"
          />
        </div>
      </template>
    </Drawer>
  </Tabs>
  <ContextMenu
    ref="workspaceContextMenu"
    appendTo="self"
    :model="[
      {
        label: 'Manage members',
        icon: 'pi pi-users',
        command: () => {
          workspaceMembersDialog = true
        }
      },
      {
        label: 'Edit workspace',
        icon: 'pi pi-pen-to-square',
        command: () => {
          dialog = 'edit'
        }
      },
      {
        label: 'Delete workspace',
        icon: 'pi pi-trash',
        command: () => {
          dialog = 'delete'
        }
      }
    ]"
  />
  <DialogWorkspaceOp v-model:action="dialog" />
  <ContextMenu
    ref="datasetContextMenu"
    appendTo="self"
    :model="[
      {
        label: 'Edit dataset',
        icon: 'pi pi-pen-to-square',
        command: () => {
          datasetDialog = 'edit'
        }
      },
      {
        label: 'Delete dataset',
        icon: 'pi pi-trash',
        command: () => {
          datasetDialog = 'delete'
        }
      }
    ]"
  />
  <DialogDatasetOp v-model:action="datasetDialog" :dataset="app.data.dataset.focused" />
  <DialogWorkspaceMembership
    v-model:visible="workspaceMembersDialog"
    :workspace="app.data.workspace.focused"
  />
  <BatchContextMenu />
</template>

<style scoped>
section:not(:first-child) {
  margin-top: 1rem;
  border-top: 1px solid var(--p-drawer-border-color);
}

.p-tab span {
  width: 40px;
  font-size: 1.2rem;
}

:deep(.p-badge) {
  font-size: 8px !important;
  line-height: 8px !important;
  height: 10px !important;
  min-width: 10px !important;
}

.row {
  align-items: baseline;

  .user-info {
    font-size: 1rem;
    width: fit-content;
    opacity: 0.7;
  }
}

.breadcrum {
  display: flex;
  flex-flow: row;
  gap: 0.3rem;
  align-items: center;
  padding: 0;

  .p-button {
    padding: 0.1rem 0.5rem;
    border-radius: 1rem;

    &:first-child {
      padding: 0.1rem;
    }
  }
}
</style>
