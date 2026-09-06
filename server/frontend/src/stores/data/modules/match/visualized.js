import { ref, computed, reactive, watch } from 'vue'
import { defineStore } from 'pinia'

import { api } from '@/api'
import { debounce, sampleInstrumentType } from '@/lib/utils'

import { useUi } from '@/stores/ui'

import { useDataset } from '../dataset'
import { useSample } from '../sample'
import { useMatchCollection, useMatchIon } from '../match'

import { useMatchParams } from './params'

// MATCH VISUALIZATION

export const useMatchVisualized = defineStore('app.data.match.visualized', () => {
  const ui = useUi()
  const matchParams = useMatchParams()
  const sample = useSample()
  const matchCollection = useMatchCollection()
  const matchIon = useMatchIon()

  // state
  const ion = ref(null)
  const isotopes = ref(null)
  const isotopeSelected = ref(null)

  // Cache for tracking current visualization context
  const cache = reactive({
    sampleId: null,
    ionId: null,
    isotopeId: null,
    collectionId: null
  })

  const instrument = computed(() => sample.focused?.instrument)
  const instrumentType = computed(() => sampleInstrumentType(sample.focused))

  // actions
  /**
   * Sets the match visualization state based on the provided sample, ion, and collection IDs.
   *
   * - If a new sample is selected, filter parameters are reset to defaults.
   * - If any of the identifiers change (sample, ion, or collection), the corresponding matches are loaded, and the visualization is activated.
   * - If parameters are provided, they override the current filter parameters.
   *
   * @param {Object} options - The parameters for setting the match visualized.
   * @param {string|null} [options.sampleId] - The ID of the sample to visualize.
   * @param {string|null} [options.ionId] - The ID of the ion to visualize.
   * @param {string|null} [options.collectionId] - The ID of the collection to visualize.
   */
  async function set({
    sampleId = cache.sampleId,
    ionId = cache.ionId,
    collectionId = cache.collectionId,
    isotopeId = null
  }) {
    console.debug('[🧬 data match.visualized] Set', {
      sampleId,
      ionId,
      collectionId,
      isotopeId
    })
    const sampleChanged = sampleId !== cache.sampleId
    const ionChanged = ionId !== cache.ionId

    // resolve ion filter params
    const ionMatchParams = matchIon.list.find((ion) => ion.target_ion_id === ionId)?.filter_params[
      sample.focused.instrument
    ]

    // Reset filter parameters if the sample or ion has changed and no new filter_parames to set are provided
    if ((sampleChanged || ionChanged) && !ionMatchParams) {
      matchParams.set()
    }

    // Update cache before "load" to prevent duplicate calls
    Object.assign(cache, { sampleId, ionId, isotopeId, collectionId })

    // Load matches and activate visualization
    await load({ sampleId, ionId, collectionId, isotopeId, init: true })
  }

  async function reload({ init } = { init: false }) {
    await load({ init })
  }

  async function clear() {
    console.debug('[🧬 data match.visualized] Clear')
    if (!ion.value) {
      return
    }
    ui.chart.clear()
    ion.value = null
    isotopes.value = null
    isotopeSelected.value = null
    Object.assign(cache, { sampleId: null, ionId: null, isotopeId: null, collectionId: null })
  }

  async function load({ sampleId, ionId, collectionId, isotopeId, init } = { init: true }) {
    isotopes.value = null
    // Resolve IDs from current state or cache
    const sample_item_id =
      sampleId ?? ion.value?.match?.sample_item_id ?? sample.focused?.sample_item_id
    const target_ion_id = ionId ?? ion.value?.target_ion_id
    const target_collection_id = collectionId ?? matchCollection.focused.target_collection_id
    const target_isotope_id = isotopeId ?? isotopeSelected.value?.target_isotope_id
    if (!sample_item_id || !target_ion_id || !target_collection_id) {
      isotopeSelected.value = null
      return
    }

    // Fetch aggregated match data with nested match structure
    const response = await api.http.post(
      `/match/aggregate/sample/${sample_item_id}/ion`,
      {
        target_ion_id,
        target_collection_id,
        match_params: matchParams.ui
      },
      {
        use: 'read',
        type: 'load_matches'
      }
    )
    if (!response?.match_ions?.[0]) {
      isotopes.value = []
      isotopeSelected.value = null
      return
    }
    // Store ion with nested match structure intact
    ion.value = response.match_ions[0]

    // Process isotopes - preserve color, format mz
    isotopes.value = response.match_isotopes.map((isotope) => ({
      ...isotope,
      // Preserve existing color if isotope was already loaded
      color:
        isotopes.value?.find((existing) => existing.target_isotope_id === isotope.target_isotope_id)
          ?.color ?? null,
      // Format mz to 4 decimal places
      mz: isotope.mz.toFixed(4)
    }))

    // Persist isotope selection if still valid
    if (target_isotope_id) {
      isotopeSelected.value =
        isotopes.value.find((iso) => iso.target_isotope_id === target_isotope_id) ?? null
    }

    // Initialize params from backend on first load
    if (init) {
      matchParams.set({ params: matchParams.db })
    }

    ui.chart.clear()

    // Load visualization data
    await api.http.post(
      `/visualization/ion_focus`,
      {},
      {
        params: {
          sample_item_id: sample_item_id,
          target_ion_id: target_ion_id,
          peak_min_intensity: matchParams.ui.peak_min_intensity,
          mz_tolerance: matchParams.ui.mz_tolerance,
          isotope_ratio_tolerance: matchParams.ui.isotope_ratio_tolerance
        },
        use: 'read',
        type: 'read_visualized_ion'
      }
    )
  }

  // Clear visualization when dataset changes
  const dataset = useDataset()
  watch(() => dataset.focused, clear)

  // Update visualization when sample focus changes (but keep same ion and collection)
  watch(
    () => sample.focused,
    async (focusedSample) => {
      console.debug('[🧬 data match.visualized] React to sample.focused')
      // Only update if we have an active visualization and a focused sample
      if (!ion.value || !focusedSample) {
        clear()
        return
      }

      const sampleId = focusedSample.sample_item_id
      const ionId = cache.ionId
      const isotopeId = cache.isotopeId
      const collectionId = cache.collectionId

      // Update visualization with new sample but same ion/collection
      await set({ sampleId, ionId, isotopeId, collectionId })
    }
  )

  return {
    // state
    ion,
    isotopes,
    isotopeSelected,
    instrument,
    instrumentType,
    // actions
    set: debounce(set),
    reload,
    clear
  }
})
