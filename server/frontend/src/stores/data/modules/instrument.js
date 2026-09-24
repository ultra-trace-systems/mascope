import { defineStore } from 'pinia'

import { api } from '@/api'

import { useData } from '@/lib/store'
import { instrumentType } from '@/lib/utils'

export const useInstrument = defineStore('app.data.instrument', () => {
  const name = 'instrument'
  const key = 'instrument'

  const data = useData(
    name,
    () =>
      api.http.get(`/instruments`, {
        use: 'read',
        type: 'load_instruments'
      }),
    {
      key,
      selection: {
        // 'binary', not 'single': one instrument at a time, but no instrument
        // is a state of its own - Raw files then lists every instrument the
        // user can see. 'single' refuses to stay unfocused and reassigns the
        // first record after every load, so "All instruments" could not be
        // chosen, let alone kept.
        mode: 'binary',
        persist: true
        // Room subscriptions are the acquisition store's: with no instrument
        // focused it needs every instrument's room, which a per-record
        // subscription cannot express.
      }
    }
  )
  /**
   * The class of an instrument by name: the one its converted files recorded,
   * when the server knows the instrument, else what the name itself says.
   * An instrument's name no longer has to say which class it is, so the list
   * is the authority and the name rule only covers a name not listed yet.
   *
   * @param {string|null|undefined} instrument An instrument name
   * @returns {'orbi'|'tof'|null}
   */
  const typeOf = (instrument) =>
    data.list.value?.find((i) => i.instrument === instrument)?.type ?? instrumentType(instrument)

  return {
    ...data,
    typeOf
  }
})
