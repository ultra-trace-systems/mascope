import { defineStore } from 'pinia'

import { api } from '@/api'

import { useData } from '@/lib/store'

import { useSample } from './sample'

export const usePeak = defineStore('app.data.peak', () => {
  const name = 'peak'
  const key = 'peak_id'

  const data = useData(
    name,
    async ({ sample_item_id }) => {
      if (!sample_item_id) {
        return []
      }
      const data = await api.http.get(`/samples/${sample_item_id}/peaks`, {
        params: {
          areas: true,
          heights: true,
          matches: true
        },
        use: 'read',
        type: 'load_sample_peaks'
      })
      if (data) {
        const { peak_id, mz, area, height, match } = data
        const records = mz.map((mz, i) => ({
          mz: mz,
          peak_id: peak_id[i],
          area: area[i],
          height: height[i],
          match: match[i]
        }))
        return records
      } else {
        return []
      }
    },
    {
      key,
      deps: () => ({
        sample_item_id: useSample().focusedId
      }),
      selection: { persist: true }
    }
  )

  return {
    ...data,
    // api
    computeAll: ({ sample_file_id }) =>
      api.http.post(
        `/sample/files/${sample_file_id}/peaks/compute`,
        {},
        {
          use: 'read',
          type: 'compute_all_sample_peaks'
        }
      )
  }
})
