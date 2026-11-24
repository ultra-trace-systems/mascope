import { defineStore } from 'pinia'

import { api } from '@/api'
import { useData } from '@/lib/store'

// The metadata field registry: the typed fields acquisition metadata is
// written under, one registry per deployment (docs/dev/
// sample_filters_and_metadata.md, section 4.4). It depends on no workspace,
// any signed-in user reads it, and the global editor role or higher writes
// it. The list holds the retired fields too, each marked `retired`: the
// registry pane shows them, and a form offering fields leaves them out.
// A change reloads every open client through the `metadata_field` record
// event.
export const useMetadataField = defineStore('app.data.metadataField', () => {
  const name = 'metadata_field'

  const data = useData(name, () =>
    api.http.get(`/metadata/fields`, {
      params: { include_retired: true },
      use: 'read',
      type: 'load_metadata_fields',
      // The registry pane shows a failed read in place of the list.
      errors: 'inline'
    })
  )

  // Writes leave their failures to the caller: the pane shows the server's
  // reason (a key taken, a field in use) beside the form that asked.
  return {
    ...data,
    create: (field) =>
      api.http.post(`/metadata/fields`, field, {
        use: 'create',
        type: 'create_metadata_field',
        errors: 'inline'
      }),
    update: (metadata_field_id, changes) =>
      api.http.patch(`/metadata/fields/${metadata_field_id}`, changes, {
        use: 'update',
        type: 'update_metadata_field',
        errors: 'inline'
      }),
    delete: (metadata_field_id) =>
      api.http.delete(`/metadata/fields/${metadata_field_id}`, {
        use: 'delete',
        type: 'delete_metadata_field',
        errors: 'inline'
      })
  }
})
