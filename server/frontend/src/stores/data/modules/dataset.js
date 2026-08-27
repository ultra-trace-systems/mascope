import { defineStore } from 'pinia'

import { api } from '@/api'

import { useData } from '@/lib/store'
import { useWorkspace } from './workspace'

export const useDataset = defineStore('app.data.dataset', () => {
  const name = 'dataset'
  const key = 'dataset_id'
  const path = () => `/workspaces/${useWorkspace().focusedId}/datasets`

  const data = useData(
    name,
    ({ workspace_id }) =>
      api.http.get(`/workspaces/${workspace_id}/datasets`, {
        use: 'read',
        type: 'load_datasets'
      }),
    {
      key,
      deps: () => ({
        workspace_id: useWorkspace().focusedId
      }),
      selection: {
        mode: 'binary',
        subscribe: true,
        persist: true
      },
      read: (dataset_id) =>
        api.http.get(`${path()}/${dataset_id}`, {
          use: 'read',
          type: 'read_dataset'
        })
    }
  )

  return {
    ...data,
    // backend
    create: (dataset) =>
      api.http.post(`${path()}`, dataset, {
        use: 'create',
        type: 'create_dataset'
      }),
    update: (dataset) =>
      api.http.patch(`${path()}/${dataset.dataset_id}`, dataset, {
        use: 'update',
        type: 'update_dataset'
      }),
    delete: (dataset) =>
      api.http.delete(`${path()}/${dataset.dataset_id}`, {
        use: 'delete',
        type: 'delete_dataset'
      }),
    move: ({ dataset_id, source_workspace_id, target_workspace_id }) =>
      api.http.post(
        `/workspaces/${source_workspace_id}/datasets/${dataset_id}/move`,
        { target_workspace_id },
        { use: 'update', type: 'move_dataset' }
      ),
    // Refresh the matches of every batch in the dataset, one batch at a time.
    // The batches decide for themselves whether they have anything to do, so
    // this is the per-batch "Refresh matches" applied across the dataset -
    // already-matched batches are skipped rather than recomputed. Progress and
    // the outcome arrive over the socket, as with the batch-level rematch.
    rematch: ({ dataset_id, full_remove = false, force = false }) =>
      api.http.post(
        `/match/rematch/dataset/${dataset_id}`,
        {},
        {
          params: { full_remove, force },
          use: 'process',
          type: 'rematch_dataset'
        }
      )
  }
})
