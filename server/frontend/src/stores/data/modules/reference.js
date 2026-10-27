import { defineStore } from 'pinia'

import { api } from '@/api'
import { useData } from '@/lib/store'
import { SPECIES_PAGE_SIZE } from '@/lib/referenceSources'

// The reference lists this deployment has loaded. They belong to the
// deployment, not to a workspace, so the list depends on nothing and any
// signed-in user reads it. Read again each time the Reference lists dialog
// opens, since an operator loads a list on the server, which sends no event.
export const useReference = defineStore('app.data.reference', () => {
  const name = 'reference'
  const key = 'name'

  const data = useData(
    name,
    () =>
      api.http.get(`/reference/sources`, {
        use: 'read',
        type: 'load_reference_sources',
        // The dialog shows a failed read in place of the list.
        errors: 'inline'
      }),
    { key }
  )

  return {
    ...data,
    /**
     * One page of the species one version of a source holds, by mass.
     * Failures are left to the caller, which shows them in place of the page.
     *
     * @param {string} source The source's name.
     * @param {{version?: string|null, page?: number, limit?: number}} [options]
     * @returns {Promise<{data: object[], total: number}>}
     */
    species: async (source, { version = null, page = 0, limit = SPECIES_PAGE_SIZE } = {}) => {
      const { data: answer } = await api.http.get(
        `/reference/sources/${encodeURIComponent(source)}/compounds`,
        {
          params: { ...(version !== null && { version }), page, limit },
          type: 'load_reference_species',
          errors: 'inline'
        }
      )
      return { data: answer?.data ?? [], total: answer?.total ?? 0 }
    }
  }
})
