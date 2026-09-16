import { ref, reactive, computed } from 'vue'
import { defineStore } from 'pinia'
import { useApp } from '@/stores'
import { ROLES } from '@/lib/roles'

export const useCollectionContextMenu = defineStore('collectionContextMenu', () => {
  const app = useApp()

  const ref_ = ref(null)
  const selection = ref(null)
  const dialog = reactive({
    op: null
  })

  const entries = computed(() => {
    // Away from a collection the one entry edits the open batch's targets, so
    // without an open batch there is nothing to offer.
    if (!selection.value) {
      if (!app.data.batch.focused) return []
      return [
        {
          label: 'Edit batch targets',
          icon: 'pi pi-bullseye',
          command: () => {
            dialog.op = 'update_targets'
          }
        }
      ]
    }

    const isGlobal = !selection.value.workspace_id
    const canMutate = !isGlobal || app.auth.user.role_id >= ROLES.admin

    return [
      {
        label: `Edit '${selection.value.target_collection_name}'`,
        icon: 'pi pi-pen-to-square',
        disabled: !canMutate,
        command: () => {
          dialog.op = 'update'
        }
      },
      {
        label: 'Edit batches',
        icon: 'pi pi-pen-to-square',
        // Batch associations are a workspace-level operation, available even
        // on global collections: the backend limits changes to batches in
        // workspaces where the user has editor rights
        command: () => {
          dialog.op = 'update_batches'
        }
      },
      {
        label: `Delete '${selection.value.target_collection_name}'`,
        icon: 'pi pi-trash',
        disabled: !canMutate,
        command: () => {
          dialog.op = 'delete'
        }
      }
    ]
  })

  const onClick = async (event) => {
    // Handle DataTable row context menu events
    const data = event?.data || selection.value
    if (data?.target_collection_id) {
      selection.value = data
      // Load detailed data for context menu operations
      await app.data.target.collection.loadDetailed(data.target_collection_id)
    }
    if (entries.value.length === 0) {
      ref_.value?.hide()
      return
    }
    ref_.value?.toggle(event.originalEvent || event)
  }

  const clear = () => {
    selection.value = null
  }

  return {
    ref: ref_,
    selection,
    dialog,
    entries,
    onClick,
    clear
  }
})
