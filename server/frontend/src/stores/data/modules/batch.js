import { defineStore } from 'pinia'

import { api } from '@/api'

import { useData } from '@/lib/store'

import { useDataset } from './dataset'

export const useBatch = defineStore('app.data.batch', () => {
  const name = 'batch'
  const key = 'sample_batch_id'

  const data = useData(
    name,
    ({ dataset_id }) =>
      api.http.get(`/sample/batches`, {
        params: { dataset_id },
        use: 'read',
        type: 'load_batches'
      }),
    {
      key,
      deps: () => ({
        dataset_id: useDataset().focusedId
      }),
      selection: {
        subscribe: true,
        persist: true
      },
      read: (sample_batch_id) =>
        api.http.get(`/sample/batches/${sample_batch_id}`, {
          use: 'read',
          type: 'read_batch'
        })
    }
  )

  return {
    ...data,
    // api
    create: (batch) =>
      api.http.post(`/sample/batches`, batch, {
        use: 'create',
        type: 'create_batch'
      }),
    update: (batch) =>
      api.http.patch(`/sample/batches/${batch.sample_batch_id}`, batch, {
        use: 'update',
        type: 'update_batch'
      }),
    delete: ({ sample_batch_id }) =>
      api.http.delete(`/sample/batches/${sample_batch_id}`, {
        use: 'process',
        type: 'delete_batch'
      }),
    copy: ({ sample_batch_id, dataset_id, sample_batch_name, sample_batch_description }) =>
      api.http.post(
        `/sample/batches/${sample_batch_id}/copy`,
        {
          dataset_id,
          sample_batch_name,
          sample_batch_description
        },
        {
          use: 'process',
          type: 'copy_batch'
        }
      ),
    importSamples: async ({ batch, sample_items }) => {
      return await api.http.post(
        `/sample/batches/${batch.sample_batch_id}/import`,
        {
          sample_items
        },
        {
          use: 'process',
          type: 'import_samples'
        }
      )
    },
    rematch: async ({ sample_batch_id, full_remove = false, force = false }) =>
      api.http.post(
        `/match/rematch/batch/${sample_batch_id}`,
        {},
        {
          params: { full_remove, force },
          use: 'process',
          type: 'rematch_batch'
        }
      ),
    rematchBatches: async ({ sample_batch_ids }) =>
      api.http.post(
        `/match/rematch/batches`,
        { sample_batch_ids },
        {},
        {
          use: 'process',
          type: 'rematch_batches'
        }
      ),
    // Launch a peak assignment run for every eligible sample in the batch.
    // Batch runs default to Stage A only, since cost scales with the number of
    // samples; pass a config with run_untargeted to widen it. Blank and
    // uncalibrated samples are skipped. Completion arrives via
    // peak_assignment_reload.
    assign: async ({ sample_batch_id, config = null }) =>
      api.http.post(
        `/peak-assignments/batch/${sample_batch_id}/assign`,
        { config },
        {
          use: 'process',
          type: 'assign_batch_peaks'
        }
      ),
    exportPeaks: async ({ sample_batch_id }) =>
      api.http.post(
        `/sample/batches/${sample_batch_id}/export_peaks`,
        {},
        {
          use: 'process',
          type: 'export_batch_peaks'
        }
      ),
    /**
     * Export sample batch and its samples' match data to Excel spreadsheet.
     *
     * Initiates a background task to generate a multi-sheet Excel file containing
     * batch metadata, samples, match compounds, and match ions. The generated file
     * is automatically downloaded when ready.
     *
     * @async
     * @param {Object} params - Export parameters
     * @param {string} params.sample_batch_id - Sample batch unique identifier
     */
    exportSpreadsheet: async ({ sample_batch_id }) => {
      return await api.http.post(
        `/sample/batches/${sample_batch_id}/export/spreadsheet`,
        {},
        {
          use: 'process',
          type: 'export_batch_spreadsheet'
        }
      )
    }
  }
})
