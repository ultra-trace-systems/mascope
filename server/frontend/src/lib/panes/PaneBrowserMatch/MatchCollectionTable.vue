<script setup>
import { computed } from 'vue'

import Button from 'primevue/button'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'

import { BaseTabbedPanel, BaseMatchTag, BaseCopyableField } from '@/lib/base'
import { num } from '@/lib/formatters'
import { collectionTypeIcons } from '@/lib/constants'
import { prettyTrim } from '@/lib/utils'

import { useApp } from '@/stores'
import { useCollectionContextMenu } from './stores'
import MatchCollectionContextMenu from './MatchCollectionContextMenu.vue'

const app = useApp()
const contextMenu = useCollectionContextMenu()

// Breadcrumb configuration - simple single level
const breadcrumb = computed(() => {
  const entityName = app.data.sample.focused
    ? app.data.sample.focused.sample_item_name
    : app.data.batch.focused?.sample_batch_name || null

  // Return empty breadcrumb to keep #menu slot right-aligned when no sample/batch focused
  if (!entityName) return { items: [] }

  return {
    items: [
      {
        icon: 'pi pi-hashtag',
        disabled: false,
        tooltip: 'Back to batch',
        action: () => app.data.sample.unfocus()
      },
      {
        icon: app.data.sample.focused ? 'pi pi-tag' : 'pi pi-hashtag',
        label: `${prettyTrim(entityName, 25)}`,
        disabled: true,
        tooltip: app.data.sample.focused
          ? `Matched collections for sample:\n ${app.data.sample.focused.sample_item_name}`
          : `Matched collections for batch:\n ${app.data.batch.focused.sample_batch_name}`
      },
      {
        icon: 'pi ph ph-crosshair',
        label: 'Target collections',
        action: () => {}, // Dummy action to switch cursor to pointer
        disabled: false,
        tooltip: "Right click to manage batch's target collections",
        contextMenu: {
          items: contextMenu.entries.value
        },
        contextMenuHandler: async (event) => {
          // Trigger "edit batch targets" context menu from breadcrumb
          await contextMenu.onClick(event)
        }
      }
    ].slice(app.data.sample.focused ? 0 : 1)
  }
})
</script>

<template>
  <BaseTabbedPanel
    :breadcrumb="breadcrumb"
    :loading="app.data.match.collection.pending"
    :error="app.data.match.collection.error"
    :onRetry="() => app.data.match.collection.load('retry')"
    :contextMenu="contextMenu"
    :pt="
      app.ui.help.right(
        `
        <h1>Match Browser: Collections</h1>

        <p>Shows the target collections associated
        with the currently selected batch, and provides
        features for managing them.</p>

        <p>
        Click on a collection to view the matched ions within the selected batch.
        </p>

        <p>
        Right click on collections to edit them or add
        them to other batches.
        </p>

        <p>
        Click on the <span class='pi pi-plus'></span> button (top right)
        to create a new target collection.
        </p>
      `,
        { doc: app.ui.help.docUrl('guides/target-collections/') }
      )
    "
  >
    <template #menu>
      <Button
        v-tooltip.top="'Create target collection'"
        label="Create target collection"
        class="hiddenlabel"
        icon="pi pi-plus"
        text
        size="small"
        @click="contextMenu.dialog.op = 'create'"
      />
    </template>
    <DataTable
      v-if="app.data.batch.focused"
      :value="app.data.match.collection.list"
      dataKey="target_collection_id"
      v-model:selection="app.data.match.collection.focused"
      selectionMode="single"
      :metaKeySelection="false"
      contextMenu
      v-model:contextMenuSelection="contextMenu.selection"
      @rowContextmenu="
        async (event) => {
          event.originalEvent.stopPropagation()
          event.originalEvent.preventDefault()
          await contextMenu.onClick(event)
        }
      "
      resizableColumns
      size="small"
      scrollable
      scrollHeight="flex"
      :virtualScrollerOptions="{ itemSize: 35.74 }"
      sortField="match.match_score"
      :sortOrder="-1"
    >
      <Column sortable sortField="match.match_score" class="match-column">
        <template #header>
          <span class="pi ph ph-seal-percent" />
        </template>
        <template #body="{ data }">
          <BaseMatchTag
            :match-score="data.match?.match_score"
            :match-category="data.match?.match_category"
            :alarming="data.match?.alarming"
            :tooltip="
              data.match?.sample_peak_intensity_sum
                ? `Peak intensity: ${num.peakIntensity.format(data.match.sample_peak_intensity_sum)} (cps)`
                : 'No peak intensity data'
            "
          />
        </template>
      </Column>
      <Column header="Collection" field="target_collection_name" sortable>
        <template #body="{ data }">
          <div :id="data.target_collection_id" class="row" style="justify-content: flex-start">
            <span
              v-if="!data.workspace_id"
              v-tooltip.top="{
                value: 'Global collection (shared across all workspaces)',
                showDelay: 500
              }"
              class="pi ph ph-globe scope-badge scope-global"
            />
            <span
              v-else
              v-tooltip.top="{
                value: `Workspace: ${app.data.workspace.list.find((w) => w.workspace_id === data.workspace_id)?.workspace_name ?? 'Unknown'}`,
                showDelay: 500
              }"
              class="pi ph ph-briefcase scope-badge scope-workspace"
            />
            <span
              :class="collectionTypeIcons[data.target_collection_type]"
              v-tooltip.top="data.target_collection_type.toLowerCase()"
            />
            <BaseCopyableField :field="data.target_collection_name" />
          </div>
        </template>
      </Column>
    </DataTable>
  </BaseTabbedPanel>
  <MatchCollectionContextMenu />
</template>

<style scoped>
.active-filter {
  visibility: visible !important;
  color: var(--p-button-text-info-color);
  opacity: 0.7;
}

.scope-badge {
  margin-right: 0.4rem;
  font-size: 0.85rem;
  opacity: 0.8;
}

.scope-global {
  color: var(--p-primary-color);
}

.scope-workspace {
  color: var(--p-primary-color);
}
</style>
