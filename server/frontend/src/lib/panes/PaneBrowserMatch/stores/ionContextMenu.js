import { ref, reactive, computed } from 'vue'
import { defineStore } from 'pinia'
import { useApp } from '@/stores'

export const useIonContextMenu = defineStore('ionContextMenu', () => {
  const app = useApp()

  const ref_ = ref(null)
  const selection = ref(null)
  const dialog = reactive({
    editCompound: false,
    removeCompound: false
  })

  const compoundLabel = computed(
    () =>
      selection.value?.target_compound_name ||
      selection.value?.target_compound_formula ||
      'Unknown Compound'
  )

  /**
   * Open an ion's match: visualize it in the focused sample and bring the Match
   * tab forward. The one way to enable that tab, shared by this menu, Enter on
   * a focused row and the row's expander button. Selecting a row does not open
   * its match: rows are multi-selectable, and the selection drives the Batch
   * chart. A match is per sample, so there is none to open without a focused one.
   */
  function openMatch(ion) {
    const sampleId = app.data.sample.focusedId
    if (!ion || !sampleId) return
    // `set` is debounced and returns before the match loads, so the tab is
    // made active up front, and enables once the visualized ion arrives.
    // Explicitly, since the tab store follows a newly visualized ion
    // everywhere but out of the Sample tab.
    app.data.match.visualized.set({
      sampleId,
      ionId: ion.target_ion_id,
      collectionId: app.data.match.collection.focusedId
    })
    app.ui.tab.active = 'match'
  }

  const entries = computed(() => {
    if (!selection.value) return []

    const collection = app.data.target.collection.detailed
    const ion = selection.value

    return [
      ...(app.data.sample.focusedId
        ? [
            {
              label: 'Open match',
              icon: 'pi ph ph-seal-question',
              command: () => openMatch(ion)
            },
            { separator: true }
          ]
        : []),
      {
        label: `Edit compound '${compoundLabel.value}'`,
        icon: 'pi pi-pen-to-square',
        command: () => {
          dialog.editCompound = true
        }
      },
      {
        label: `Remove '${compoundLabel.value}' from '${collection?.target_collection_name || 'collection'}'`,
        icon: 'pi pi-minus',
        command: () => {
          dialog.removeCompound = true
        },
        disabled: !collection
      }
    ]
  })

  const onClick = async (event) => {
    selection.value = event.data
    ref_.value?.toggle(event.originalEvent)
  }

  const clear = () => {
    selection.value = null
  }

  return {
    ref: ref_,
    selection,
    dialog,
    entries,
    compoundLabel,
    openMatch,
    onClick,
    clear
  }
})
