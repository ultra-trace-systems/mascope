import { ref, computed, watch } from 'vue'
import { defineStore } from 'pinia'

import { FilterMatchMode } from '@primevue/core/api'
import { useApp } from '@/stores'

export const useBatchTableConfig = defineStore('browser.sample.batchTable', () => {
  const app = useApp()

  // Filtered batch list - synced from BatchTable component
  const filteredBatchList = ref([])

  const config = ref({
    sortField: 'sample_batch_name',
    sortOrder: -1,
    filters: {
      global: { value: null, matchMode: FilterMatchMode.CONTAINS }
    }
  })

  const defaultConfig = {
    sortField: 'sample_batch_name',
    sortOrder: -1,
    filters: {
      global: { value: null, matchMode: FilterMatchMode.CONTAINS }
    }
  }

  const isDefault = computed(() => JSON.stringify(config.value) === JSON.stringify(defaultConfig))

  // local storage persistence

  const storageKey = computed(() => `sample-browser-dataset[${app.data.dataset.focusedId}]`)

  // write to local storage - or clear the key when the config is back to its
  // default. Skipping the write for a default config used to leave whatever was
  // stored earlier behind, so sorting back to the default order kept restoring
  // the previous, non-default one on the next load.
  function writeConfig() {
    if (!app.data.dataset.focusedId) return

    if (isDefault.value) {
      localStorage.removeItem(storageKey.value)
    } else {
      localStorage.setItem(storageKey.value, JSON.stringify(config.value))
    }
  }

  // read from local storage, falling back on default
  function readConfig() {
    const storedState = localStorage.getItem(storageKey.value)
    const defaultState = JSON.stringify(defaultConfig)
    config.value = JSON.parse(storedState ?? defaultState)
  }

  // reset to default config and clear local storage
  function resetConfig() {
    config.value = structuredClone(defaultConfig)
    localStorage.removeItem(storageKey.value)
  }

  // write to local storage when any options update
  watch(() => config.value, writeConfig, { deep: true })

  // read from local storage when dataset changes
  watch(
    () => app.data.dataset.focusedId,
    () => {
      if (app.data.dataset.focusedId) {
        readConfig()
      }
    },
    { immediate: true }
  )

  // Apply sorting to filtered list for consistent navigation
  const sortedFilteredBatchList = computed(() => {
    const list = [...filteredBatchList.value]
    const { sortField, sortOrder } = config.value

    if (!sortField) return list

    return list.sort((a, b) => {
      const aVal = a[sortField]
      const bVal = b[sortField]

      if (aVal == null && bVal == null) return 0
      if (aVal == null) return sortOrder
      if (bVal == null) return -sortOrder

      if (typeof aVal === 'string') {
        return sortOrder * aVal.localeCompare(bVal)
      }

      return sortOrder * (aVal - bVal)
    })
  })

  return {
    config,
    filteredBatchList,
    sortedFilteredBatchList,
    resetConfig
  }
})
