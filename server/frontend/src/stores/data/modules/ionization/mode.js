import { defineStore } from 'pinia'

import { api } from '@/api'
import { useData } from '@/lib/store'

export const useIonizationMode = defineStore('app.data.ionization.mode', () => {
  const name = 'ionization_mode'
  const key = 'ionization_mode_id'

  const data = useData(
    name,
    () =>
      api.http.get(`/ionization/modes`, {
        use: 'read',
        type: 'load_ionization_modes'
      }),
    {
      key
    }
  )

  return {
    ...data,
    // api
    read: (ionization_mode_id) =>
      api.http.get(`/ionization/modes/${ionization_mode_id}`, {
        use: 'read',
        type: 'read_ionization_mode'
      }),
    // The whole payload, as `update` below already does. Naming the fields one
    // by one meant a field added to the form and to the API was dropped here
    // silently - the POST succeeded and the mode was stored without it. That is
    // how the instrument a mode belongs to went missing (#1463), so the list is
    // gone rather than corrected.
    create: (ionization_mode) =>
      api.http.post(`/ionization/modes`, ionization_mode, {
        use: 'create',
        type: 'create_ionization_mode'
      }),
    update: (ionization_mode_id, ionization_mode_update) =>
      api.http.patch(`/ionization/modes/${ionization_mode_id}`, ionization_mode_update, {
        use: 'update',
        type: 'update_ionization_mode'
      }),
    delete: (ionization_mode_id) =>
      api.http.delete(`/ionization/modes/${ionization_mode_id}`, {
        use: 'delete',
        type: 'delete_ionization_mode'
      })
  }
})
