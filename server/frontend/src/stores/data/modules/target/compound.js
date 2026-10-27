import { defineStore } from 'pinia'

import { api } from '@/api'
import { useData } from '@/lib/store'

export const useTargetCompound = defineStore('app.data.target.compound', () => {
  const name = 'target_compound'

  const data = useData(name, () =>
    api.http.get(`/target/compounds`, {
      use: 'read',
      type: 'load_target_compounds'
    })
  )

  return {
    ...data,
    // backend methods
    update: (compound) =>
      api.http.patch(`/target/compounds/`, [compound], {
        use: 'update',
        type: 'update_target_compound'
      }),
    // One page of the reference rows that could give a compound of `formula`
    // its structure, with `results` (the rows on every page). The CAS number
    // and the name put their rows first. Failures are the caller's to show.
    findStructures: async ({ formula, cas_number, name, page = 0, limit }) =>
      (
        await api.http.get(`/target/compounds/structures`, {
          params: {
            formula,
            cas_number: cas_number?.trim() || undefined,
            name: name?.trim() || undefined,
            page,
            limit
          },
          type: 'find_target_compound_structures',
          errors: 'inline'
        })
      ).data
  }
})
