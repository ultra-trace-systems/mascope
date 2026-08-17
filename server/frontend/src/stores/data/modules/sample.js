import { defineStore } from 'pinia'

import { api } from '@/api'
import { getApiErrorMessage } from '@/api/utils'

import { genId } from '@/lib/utils'
import { useData } from '@/lib/store'

import { useUi } from '../../ui'

import { useBatch } from './batch'

const FILE_UPLOAD_TIMEOUT = 600_000

export const useSample = defineStore('app.data.sample', () => {
  const name = 'sample'
  const key = 'sample_item_id'

  const data = useData(
    name,
    (params) =>
      api.http.get(`/samples`, {
        params: {
          sample_batch_id: params.sample_batch_id
        },
        use: 'read',
        type: 'load_samples'
      }),
    {
      key,
      events: ['match_reload'], // Cross-store event (match updates trigger sample reload)
      deps: () => ({
        sample_batch_id: useBatch().focusedId
      }),
      selection: {
        mode: 'multiple',
        subscribe: ({ sample_item_id }) => sample_item_id,
        persist: true
      },
      read: (sample_item_id) =>
        api.http.get(`/samples/${sample_item_id}`, {
          use: 'read',
          type: 'read_sample'
        })
    }
  )

  return {
    ...data,
    // api
    create: (sample) =>
      api.http.post(`/sample/items`, sample, {
        use: 'create',
        type: 'create_sample'
      }),
    update: ({ sample }) =>
      api.http.patch(
        `/sample/items/${sample.sample_item_id}`,
        { sample_item: sample },
        {
          use: 'update',
          type: 'update_sample'
        }
      ),
    delete: ({ sample_item_ids }) =>
      api.http.post(
        `/sample/items/delete`,
        { sample_item_ids },
        {
          use: 'delete',
          type: 'delete_sample'
        }
      ),
    copy: ({ sample_item_ids, sample_batch_id }) =>
      api.http.post(
        `/sample/items/copy`,
        {
          sample_batch_id,
          sample_item_ids
        },
        {
          use: 'process',
          type: 'copy_sample'
        }
      ),
    move: ({ sample_item_ids, sample_batch_id }) =>
      api.http.post(
        `/sample/items/move`,
        {
          sample_batch_id,
          sample_item_ids
        },
        {
          use: 'process',
          type: 'move_sample'
        }
      ),
    process: async (sample_item) => {
      return await api.http.post(`/sample/items/process`, sample_item, {
        use: 'process',
        type: 'process_samples'
      })
    },
    match: ({ sample_item_id }) =>
      api.http.post(
        `/match/compute/sample/${sample_item_id}`,
        {},
        {
          use: 'process',
          type: 'compute_match_sample'
        }
      ),
    rematch: async ({ sample_item_id, full_remove = false }) =>
      api.http.post(
        `/match/rematch/sample/${sample_item_id}`,
        {},
        {
          params: { full_remove },
          use: 'process',
          type: 'rematch_sample'
        }
      ),
    exportPeaks: async ({ sample_item_id }) =>
      api.http.post(
        `/sample/items/${sample_item_id}/export_peak_data`,
        {},
        {
          use: 'process',
          type: 'export_sample_peaks'
        }
      ),
    upload: async (files) => {
      const ui = useUi()
      const mainProcessId = genId(8) // Generate a unique ID for the overall upload process

      try {
        // Create FormData for sample files batch upload
        const formData = new FormData()
        files.forEach((file) => formData.append('files', file))

        // Initial notification
        ui.notification.push({
          type: 'sample_file_upload',
          process_id: mainProcessId,
          status: 'pending',
          message: `Uploading ${files.length} file${files.length > 1 ? 's' : ''}...`,
          progress: 0
        })

        // Make sample files batch upload request with progress tracking
        const response = await api.http.post('/sample/files/upload', formData, {
          timeout: FILE_UPLOAD_TIMEOUT * Math.max(files.length, 1),
          headers: { 'Content-Type': 'multipart/form-data' },
          onUploadProgress: ({ progress }) => {
            const percentCompleted = progress * 100
            ui.notification.push({
              type: 'sample_file_upload',
              process_id: mainProcessId,
              status: 'pending',
              message: `Uploading ${files.length} file${files.length > 1 ? 's' : ''}... ${percentCompleted.toFixed(1)}%`,
              progress: percentCompleted
            })
          }
        })

        if (response?.status === 201) {
          const { message, status, data } = response.data

          // Success notification
          ui.notification.push({
            type: 'sample_file_upload',
            process_id: mainProcessId,
            status: status === 'partial' ? 'warning' : 'success',
            message,
            progress: 100
          })

          // Show individual failed file notifications for partial uploads
          if (status === 'partial' && data?.failed_uploads) {
            data.failed_uploads.forEach(({ filename, error }) => {
              ui.notification.push({
                type: 'sample_file_upload',
                status: 'error',
                message: `${filename}: ${error}`
              })
            })
          }

          return { data, resolved: true }
        }

        throw new Error(`Unexpected response status: ${response.status}`)
      } catch (error) {
        ui.notification.push({
          type: 'sample_file_upload',
          process_id: mainProcessId,
          status: 'error',
          message: getApiErrorMessage(error, 'Failed to upload files'),
          progress: 100
        })

        return { data: null, resolved: false }
      }
    }
  }
})
