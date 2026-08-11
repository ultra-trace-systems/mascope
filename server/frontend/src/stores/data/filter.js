import { shallowRef } from 'vue'
import { defineStore } from 'pinia'

// selectable data modules that would be added to filter store
export const useFilter = defineStore(`app.filter`, () => {
  const batch = shallowRef([])
  const instrument = shallowRef([])
  const ionization_mechanism = shallowRef([])
  const peak = shallowRef([])
  const sample = shallowRef([])
  const target_collection = shallowRef([])
  const match_collection = shallowRef([])
  const match_ion = shallowRef([])
  const peak_assignment_run = shallowRef([])
  const peak_assignment = shallowRef([])
  const dataset = shallowRef([])

  const allFilters = {
    instrument,
    dataset,
    batch,
    sample,
    target_collection,
    match_collection,
    match_ion,
    peak_assignment_run,
    peak_assignment,
    ionization_mechanism,
    peak
  }

  return {
    ...allFilters
  }
})
