import { defineStore } from 'pinia'

import { api } from '@/api'
import { useData } from '@/lib/store'

export const useIonizationMechanism = defineStore('app.data.ionization.mechanism', () => {
  const name = 'ionization_mechanism'
  const key = 'ionization_mechanism_id'

  const data = useData(
    name,
    () =>
      api.http.get(`/ionization_mechanisms`, {
        use: 'read',
        type: 'load_ionization_mechanisms'
      }),
    {
      key,
      selection: true
    }
  )

  return {
    ...data,
    // api
    read: (ionization_mechanism_id) =>
      api.http.get(`/ionization_mechanisms/${ionization_mechanism_id}`, {
        use: 'read',
        type: 'read_ionization_mechanism'
      }),
    create: ({ ionization_mechanism, name = null }) =>
      api.http.post(
        `/ionization_mechanisms`,
        {
          ionization_mechanism,
          name
        },
        {
          use: 'create',
          type: 'create_ionization_mechanism'
        }
      ),
    delete: (ionization_mechanism_id) =>
      api.http.delete(`/ionization_mechanisms/${ionization_mechanism_id}`, {
        use: 'delete',
        type: 'delete_ionization_mechanism'
      })
  }
})
