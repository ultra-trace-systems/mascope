import { ref, reactive } from 'vue'

import { api } from '@/api'

import { useApp } from '@/stores'

import { applyInstrumentDefaults } from '@/lib/calibrationDefaults'

export const useMzFit = ({ unmount } = { unmount: false }) => {
  const app = useApp()

  // Seed values until the instrument defaults are fetched for a sample.
  const DEFAULT_MZ_CALIBRATION_PARAMS = {
    match_score_min: 0,
    isotope_abundance_min: 0.15,
    peak_intensity_min: 0, //1000,
    refine_window: 100,
    snr_threshold: 10.0
  }

  // state
  const active = ref(null)
  const status = ref(null)
  const current = ref(null)
  const error = ref(null)
  const stats = ref(null)
  const affectedBatches = ref([])
  const affectedSamples = ref([])
  const mzCalibrationParams = reactive({ ...DEFAULT_MZ_CALIBRATION_PARAMS })
  // Defaults the current parameter values were seeded from; used to tell
  // user-modified fields apart when instrument defaults arrive.
  let paramsBaseline = { ...DEFAULT_MZ_CALIBRATION_PARAMS }

  let defaultsSampleId = null

  async function loadInstrumentDefaults(sample) {
    // Instrument-appropriate defaults (Orbitrap vs TOF differ an order of
    // magnitude in refine window and SNR threshold). User-modified fields
    // are preserved; untouched fields follow the sample's instrument.
    if (!sample?.sample_item_id || sample.sample_item_id === defaultsSampleId) return
    const fetched = (
      await api.http.get(`/calibration/default_params`, {
        params: {
          sample_item_id: sample.sample_item_id
        },
        use: 'read',
        type: 'read_calibration_default_params'
      })
    )?.params
    if (!fetched) return
    Object.assign(
      mzCalibrationParams,
      applyInstrumentDefaults(mzCalibrationParams, paramsBaseline, fetched)
    )
    paramsBaseline = { ...fetched }
    defaultsSampleId = sample.sample_item_id
  }

  async function load(sample) {
    current.value =
      (await api.http.get(`/calibration/mz_calibration`, {
        params: {
          sample_item_id: sample.sample_item_id
        },
        use: 'read',
        type: 'read_mz_calibration'
      })) ?? current.value
    await loadInstrumentDefaults(sample)
    active.value = sample
  }

  async function unload() {
    active.value = null
    status.value = null
    current.value = null
    error.value = null
    stats.value = null
    affectedBatches.value = []
    affectedSamples.value = []
  }

  async function compute(sample) {
    await unload()
    const { sample_item_id } = sample ?? active.value
    await api.http.post(`/calibration/mz_fit`, mzCalibrationParams, {
      params: { sample_item_id },
      use: 'process',
      type: 'mz_fit'
    })
  }

  async function apply(sample) {
    const { filename } = sample ?? active.value
    await api.http.post(
      `/calibration/mz_apply`,
      { fit: current.value },
      {
        params: { filename },
        use: 'process',
        type: 'apply_mz_fit'
      }
    )
  }

  const handler = app.ui.notification.on('calibration_mz_fit', (payload) => {
    status.value = payload?.status
    if (payload?.status === 'success') {
      current.value = payload?.data?.fit
      stats.value = payload?.data?.stats
      affectedBatches.value = payload?.data?.affected_sample_batch_ids ?? []
      affectedSamples.value = payload?.data?.affected_sample_item_ids ?? []
    }
    if (payload?.status === 'error') {
      error.value = payload?.message
      // Critical errors prevent further steps
      stats.value = null
      current.value = null
      affectedBatches.value = []
      affectedSamples.value = []
    }
    if (payload?.status === 'warning') {
      error.value = payload?.message
      // Allow further steps if stats are available
      current.value = payload?.error?.detail?.data?.fit
      stats.value = payload?.error?.detail?.data?.stats
      affectedBatches.value = payload?.error?.detail?.data?.affected_sample_batch_ids ?? []
      affectedSamples.value = payload?.error?.detail?.data?.affected_sample_item_ids ?? []
    }
  })
  if (unmount) {
    handler.unmount()
  }

  return reactive({
    // state
    status,
    current,
    error,
    stats,
    affectedBatches,
    affectedSamples,
    mzCalibrationParams,
    // actions
    load,
    loadInstrumentDefaults,
    unload,
    compute,
    apply
  })
}
