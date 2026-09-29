import { ref, computed } from 'vue'
import { defineStore } from 'pinia'

import { api } from '@/api'
import { useAuth } from '@/stores/auth'
import { useMatchVisualized } from './visualized'

export const useMatchParams = defineStore('app.data.match.params', () => {
  const matchVisualized = useMatchVisualized()

  const defaults = ref()

  const auth = useAuth()

  // TODO: make global params store
  // Load default params on login
  auth.onLogin(() => {
    api.http
      .get('/params', {
        type: 'read_params'
      })
      .then(({ data }) => {
        defaults.value = data?.data?.params.match
      })
  })

  // Computed defaults for current instrument type
  const typeDefaults = computed(() =>
    defaults.value ? defaults.value[matchVisualized.instrumentType] : null
  )
  const areDefault = computed(() => {
    if (matchVisualized.instrumentType && typeDefaults.value) {
      return Object.keys(typeDefaults.value).every(
        (key) => ui.value[key] === typeDefaults.value[key]
      )
    } else {
      return false
    }
  })

  // frontend param state
  const ui = ref()

  // Backend param state (what's stored in DB for this ion)
  const db = computed(() => ({
    ...(typeDefaults.value ?? {}),
    ...(matchVisualized.ion?.filter_params[matchVisualized.instrument] ?? {})
  }))

  // Check if UI params differ from DB params
  const changed = computed(() =>
    ui.value && db.value
      ? Object.keys(db.value).some((key) => db.value[key] !== ui.value[key])
      : null
  )

  // actions
  function set(opts) {
    ui.value = {
      ...typeDefaults.value,
      ...(opts?.params ?? {})
    }
  }

  function reset() {
    // set defaults
    set()
    matchVisualized.reload()
  }

  function revert() {
    // set initial
    set({ params: db.value })
    matchVisualized.reload()
  }

  async function save() {
    await api.http.patch(
      `/target/ions/${matchVisualized.ion?.target_ion_id}`,
      {
        match_params: {
          [matchVisualized.instrument]: ui.value
        }
      },
      {
        use: 'update',
        type: 'save_ion_match_params'
      }
    )
    await matchVisualized.reload()
  }
  async function remove() {
    await api.http.patch(
      `/target/ions/${matchVisualized.ion.target_ion_id}`,
      {
        delete_instrument_params: matchVisualized.instrument
      },
      {
        use: 'delete',
        type: 'remove_ion_match_params'
      }
    )
    // Drop the removed params from the UI, then reload once. Calling reset()
    // here would start a second load: it does not await the one it fires, and
    // a load runs as far as clearing the isotopes before it awaits, so the
    // reload below would find no previous rows to carry the colours off.
    set()
    await matchVisualized.reload({ init: true })
  }

  /**
   * Compute match category using UI param threshold values
   * @param {object} record - Record with match_score and match_category
   * @returns {number} - Computed match category (0, 1, or 2)
   */
  function uiCategory(record) {
    if (!ui.value) {
      return record?.match_category
    }
    let match_category = 0
    if (record?.match_score > ui.value.possible_match_threshold) {
      match_category = 1
    }
    if (record?.match_score > ui.value.probable_match_threshold) {
      match_category = 2
    }
    return match_category
  }

  return {
    ui,
    db,
    typeDefaults,
    default: areDefault,
    changed,
    set,
    reset,
    revert,
    save,
    remove,
    uiCategory
  }
})
